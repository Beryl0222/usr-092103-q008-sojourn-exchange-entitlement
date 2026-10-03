"""年度点数账户：每笔增减都记录原因与来源引用，会员可逐笔核对。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    account_id: str
    delta: int               # 正为增加，负为扣减
    balance_after: int
    reason_code: str         # annual_grant / reservation_confirm / cancel_refund / ...
    refs: dict               # 来源引用：reservation_id、compensation_id、contract_id 等
    occurred_at: str
    summary: str


class InsufficientPoints(Exception):
    """可用点数不足。"""


class PointsLedger:
    def __init__(self) -> None:
        self._entries: dict[str, list[LedgerEntry]] = {}
        self._seq = 0

    def balance(self, account_id: str) -> int:
        entries = self._entries.get(account_id, [])
        return entries[-1].balance_after if entries else 0

    def _append(self, account_id: str, delta: int, reason_code: str,
                occurred_at: str, refs: dict | None, summary: str) -> LedgerEntry:
        self._seq += 1
        balance = self.balance(account_id) + delta
        if balance < 0:
            self._seq -= 1
            raise InsufficientPoints(f"账户 {account_id} 点数不足：{summary}")
        entry = LedgerEntry(
            entry_id=f"pt-{self._seq:06d}",
            account_id=account_id,
            delta=delta,
            balance_after=balance,
            reason_code=reason_code,
            refs=dict(refs or {}),
            occurred_at=occurred_at,
            summary=summary,
        )
        self._entries.setdefault(account_id, []).append(entry)
        return entry

    def grant(self, account_id: str, amount: int, occurred_at: str,
              refs: dict | None = None, summary: str = "年度点数发放") -> LedgerEntry:
        return self._append(account_id, amount, "annual_grant", occurred_at, refs, summary)

    def debit(self, account_id: str, amount: int, reason_code: str, occurred_at: str,
              refs: dict | None = None, summary: str = "") -> LedgerEntry:
        return self._append(account_id, -amount, reason_code, occurred_at, refs, summary)

    def credit(self, account_id: str, amount: int, reason_code: str, occurred_at: str,
               refs: dict | None = None, summary: str = "") -> LedgerEntry:
        return self._append(account_id, amount, reason_code, occurred_at, refs, summary)

    def statement(self, account_id: str) -> tuple[LedgerEntry, ...]:
        """会员对账单：逐笔说明点数为何增加或扣减。"""
        return tuple(self._entries.get(account_id, ()))
