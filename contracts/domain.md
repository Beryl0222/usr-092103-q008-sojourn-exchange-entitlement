# 旅居村换住权益领域设计

本文档定义换住权益后端的聚合、事件与横切规则。信封格式见 `domain.schema.json`，可执行校验见 `../src/validator.py`，贯穿全流程的事件流样例见 `../data/samples/`。

## 1. 信封与身份

每个领域事件携带：

| 字段 | 含义 |
|---|---|
| `event_id` | 全局唯一事件 ID，同时是事件存储的幂等键 |
| `event_type` / `aggregate_type` / `aggregate_id` | 事件类型与所属聚合 |
| `version` | 该聚合上的事件序号，从 1 严格递增；写入时作为乐观并发令牌（期望版本），版本不符即拒绝并重查 |
| `occurred_at` | 服务端受理时间（ISO 8601 带时区） |
| `summary` | 中文一句话摘要 |
| `trace_id`（可选） | 一次换住全程的追踪 ID，跨聚合串联授权、占用、入住、补偿 |
| `causation_id`（可选） | 直接成因事件的 `event_id` |

## 2. 聚合

| 聚合 | 职责 | 关键不变式 |
|---|---|---|
| `property_authorization` | 房源授权：归属、可住人数、淡旺季额度、管家服务等级、授权期限 | 同一房源同一时刻仅一条有效授权 |
| `property_calendar` | 房态版本：占用区间（长租/短换/自住）与封房区间 | 三类占用区间互不重叠；每次变更 `version` +1 |
| `member_contract` | 会员合同：等级、有效期、退改责任条款 | 合同条款是退改与补偿的唯一依据 |
| `member_entitlement` | 年度点数账户与流水 | 余额 = 发放 − 扣减 + 返还 ± 调整；流水只增不改 |
| `stay_reservation` | 预约占用与入住验真状态机 | 状态机见 §4.2；入住按幂等键去重 |
| `service_delegation` | 服务委托：谁在何段入住内可见哪些字段 | 可见性严格限于本次入住范围 |
| `compensation_entry` | 补偿单：改期 / 替代房 / 点数返还 | 只新增并链接原预约，绝不覆盖 |

## 3. 事件目录

| 事件 | 聚合 | 必填负载（摘要） | 说明 |
|---|---|---|---|
| PROPERTY_AUTHORIZED | property_authorization | property_id, owner_id, village_id, capacity, season_quotas, housekeeper_service | 房源授权入池 |
| PROPERTY_AUTHORIZATION_UPDATED | property_authorization | 同上变更字段 | 调整额度/人数/服务等级 |
| PROPERTY_AUTHORIZATION_REVOKED | property_authorization | property_id | 授权退出换住池 |
| CALENDAR_BLOCK_REGISTERED | property_calendar | property_id, block_id, block_type, start_date, end_date | 登记占用：long_rental / swap_stay / owner_use |
| CALENDAR_BLOCK_RELEASED | property_calendar | property_id, block_id | 释放占用区间 |
| PROPERTY_BLOCKED | property_calendar | property_id, block_id, reason, start_date, end_date | 维修/自然灾害封房，只影响指定日期与房源 |
| PROPERTY_BLOCK_LIFTED | property_calendar | property_id, block_id | 解除封房 |
| MEMBER_CONTRACT_SIGNED | member_contract | contract_id, member_id, tier, cancel_policy, valid_from, valid_to | 签订会员合同 |
| MEMBER_CONTRACT_TERMINATED | member_contract | contract_id, member_id | 合同终止 |
| ENTITLEMENT_GRANTED | member_entitlement | entitlement_id, member_id, contract_id, annual_points, period | 发放年度点数 |
| POINTS_DEBITED | member_entitlement | entitlement_id, amount, reason_code, ref_aggregate_id, balance_after | 扣点（预约确认、违约扣罚等） |
| POINTS_REFUNDED | member_entitlement | 同上 | 返还（提前离店、补偿等） |
| POINTS_ADJUSTED | member_entitlement | 同上 | 人工调整，必须注明成因 |
| RESERVATION_HELD | stay_reservation | reservation_id, journey_id, property_id, member_id, start_date, end_date, points_amount, hold_expires_at, calendar_version | 单段持有，原子确认的第一阶段 |
| RESERVATION_CONFIRMED | stay_reservation | reservation_id, journey_id, property_id, member_id, start_date, end_date, points_amount | 确认；同行人与健康偏好随本事件入服务范围 |
| RESERVATION_HOLD_RELEASED | stay_reservation | reservation_id, journey_id, reason | 持有释放（他段失败或超时） |
| RESERVATION_CANCELLED | stay_reservation | reservation_id, cancel_reason, liability | 取消；liability ∈ member / operator / force_majeure |
| RESERVATION_RESCHEDULED | stay_reservation | reservation_id, supersedes_reservation_id, start_date, end_date | 改期，新预约链接原预约 |
| RESERVATION_SUBSTITUTED | stay_reservation | reservation_id, substitutes_reservation_id, property_id, start_date, end_date | 替代房，新预约链接原预约 |
| STAY_CHECKED_IN | stay_reservation | reservation_id, idempotency_key, verified_by, verification_method, recorded_offline, captured_at | 入住验真，支持离线 |
| STAY_CHECKED_OUT | stay_reservation | reservation_id, early_departure, actual_checkout_date | 离店，标记是否提前 |
| SERVICE_DELEGATED | service_delegation | delegation_id, reservation_id, delegate_id, scope_start, scope_end, visible_fields | 委托管家并限定可见字段 |
| SERVICE_DELEGATION_REVOKED | service_delegation | delegation_id | 撤销委托 |
| COMPENSATION_ISSUED | compensation_entry | compensation_id, origin_reservation_id, kind | 按合同生成补偿；kind ∈ reschedule / substitute / points_refund |
| COMPENSATION_FULFILLED | compensation_entry | compensation_id | 补偿履行完毕 |

## 4. 横切规则

### 4.1 占用不重叠（房态版本 CAS）

长期租住（long_rental）、短期换住（swap_stay）、房东自用（owner_use）三类区间在同一房源上不得重叠。写入 `property_calendar` 时携带期望 `version`（compare-and-swap）：版本不符说明存在并发占用，拒绝后重查重试。维修/灾害封房（PROPERTY_BLOCKED）不受此限——它合法覆盖既有占用并触发补偿（§4.4），且只影响指定的房源与日期区间。

### 4.2 跨村原子确认（持有—确认两段式）

一段旅程（`journey_id`）跨多个村落时：

1. 每段在对应村落的房态上写 RESERVATION_HELD（含 `calendar_version` 与 `hold_expires_at`）；
2. 全部段落持有成功 → 逐段发 RESERVATION_CONFIRMED；任一段失败或超时 → 已持有段落发 RESERVATION_HOLD_RELEASED 释放，旅程整体不成立；
3. 确认后按行程一次性 POINTS_DEBITED。

预约状态机：`HELD → CONFIRMED → CHECKED_IN → CHECKED_OUT`；旁路：`HOLD_RELEASED` / `CANCELLED` / `RESCHEDULED` / `SUBSTITUTED`。

### 4.3 离线入住去重（幂等键）

管家端离线办理入住时生成 `idempotency_key`（预约号 + 入住日期 + 设备流水），记录 `captured_at` 与 `recorded_offline=true`；恢复联网后上报。服务端以 `idempotency_key` 去重：重复上报返回首次受理结果，不重复产生 STAY_CHECKED_IN，不重复触发结算。

### 4.4 封房与补偿（不覆盖原预约）

维修或自然灾害只影响 PROPERTY_BLOCKED 指定的房源与日期区间。命中的已确认预约按合同退改条款生成补偿单（COMPENSATION_ISSUED），三选一：

- **改期**：新预约 RESERVATION_RESCHEDULED，`supersedes_reservation_id` 链接原预约；
- **替代房**：新预约 RESERVATION_SUBSTITUTED，`substitutes_reservation_id` 链接原预约；
- **点数返还**：POINTS_REFUNDED 入账。

原预约以 RESERVATION_CANCELLED（`liability=operator` 或 `force_majeure`）收尾，历史完整保留，绝不就地修改。补偿履行后发 COMPENSATION_FULFILLED 关单。

### 4.5 隐私范围（同行人与健康偏好）

同行人（`party`）与健康偏好（`health_preferences`）只随 RESERVATION_CONFIRMED 进入本次服务范围，标记 `privacy_scope=service_only`。SERVICE_DELEGATED 的 `visible_fields` 枚举受托人在本次入住内可见的字段；房东与管家仅见本次入住范围，按会员跨行程的查询对房东角色拒绝。会员本人可见自己的全部数据。

### 4.6 点数流水可核对

POINTS_DEBITED / POINTS_REFUNDED / POINTS_ADJUSTED 均携带 `amount`、`reason_code`、`ref_aggregate_id`（成因预约或补偿单）、`balance_after`。会员可逐笔核对点数增减原因；「当前房源由谁提供」= 预约 `property_id` → `property_authorization.owner_id`。

### 4.7 全程追踪

运营方以 `trace_id` 取回一次换住的全部事件，以 `causation_id` 沿成因链反查（补偿单 → 封房事件 → 预约 → 授权），以 `(aggregate_id, version)` 重放任一聚合的历史。

## 5. 兼容性

既有五种事件（PROPERTY_AUTHORIZED、ENTITLEMENT_GRANTED、RESERVATION_CONFIRMED、STAY_CHECKED_IN、COMPENSATION_ISSUED）语义不变；新增事件类型与聚合向后兼容，信封仍允许扩展字段（`additionalProperties: true`）。
