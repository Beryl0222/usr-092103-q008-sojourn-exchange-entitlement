# 旅居村换住权益

本仓库保存旅居村换住权益的领域词汇、交换事件与中文联调样例，供后续服务在统一身份和版本语义下协作。

## 资料结构

- `contracts/domain.schema.json`：领域事件的公共信封与稳定枚举。
- `contracts/domain.md`：聚合职责、事件目录与横切规则（占用不重叠、跨村原子确认、离线入住去重、封房补偿链、隐私范围、点数可核对、全程追踪）。
- `data/sample.json`：一条最小业务事件样例。
- `data/samples/`：一次国庆跨村换住的完整事件流样例（授权→持有→确认→入住→封房→补偿）。
- `src/`：公共字段与事件负载的基础校验代码。
- `tests/`：验证样例能够通过基础约定。

核心对象为七类聚合：property_authorization（房源授权）、property_calendar（房态版本）、member_contract（会员合同）、member_entitlement（年度点数）、stay_reservation（预约占用与入住验真）、service_delegation（服务委托）、compensation_entry（补偿单）。已登记 25 种事件类型，详见 `contracts/domain.md`。这些内容只规定跨模块交换的起点，不包含具体业务流程、存储或接口实现。

## 本地检查

```bash
python3 -m unittest discover -s tests
```
