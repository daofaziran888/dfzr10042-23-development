"""供应商归属对账：把「按业务键关联」升级为「按合同归属与生效日核验」。

背景
----
原先把供应商明细与订单按业务键（订单号）直接关联，同一个订单号在甲公司、
乙公司各有一条明细时，只按业务键无法判断哪条才该进本期应付。本模块提供一层
可追溯的归属核验，结论只有一种：某条业务明细在某个对账期是否具备「入账资格」。

核验顺序（先到先得，决定拒绝原因的唯一口径）
------------------------------------------------
1. ``OUT_OF_PERIOD``   交易日期不在对账期
2. ``NO_CONTRACT``     找不到能覆盖该公司/部门/交易的合同
3. ``EXPIRED_ORDER``   交易日超出合同有效期（含跨月结束日之后的订单）
4. ``WRONG_DEPARTMENT``合同归属部门与报表部门不一致
5. ``CONTRACT_REVOKED``合同更正记录生效日早于交易，对应资格被撤回
6. ``UNAUTHORIZED``    关联在签发前已被判定越权
7. ``REMOVED``         账单上已剔除该行

只有 ``ELIGIBLE`` 的行会形成入账资格。所有判断都是纯函数，方便在报表、账单、
测试里复算出同一口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# 拒绝原因（同时也是前端筛选值与文案 key）
ELIGIBLE = "ELIGIBLE"
OUT_OF_PERIOD = "OUT_OF_PERIOD"
NO_CONTRACT = "NO_CONTRACT"
EXPIRED_ORDER = "EXPIRED_ORDER"
WRONG_DEPARTMENT = "WRONG_DEPARTMENT"
CONTRACT_REVOKED = "CONTRACT_REVOKED"
UNAUTHORIZED = "UNAUTHORIZED"
REMOVED = "REMOVED"

REJECTION_LABELS: dict[str, str] = {
    ELIGIBLE: "合格",
    OUT_OF_PERIOD: "不在对账期",
    NO_CONTRACT: "无合同归属",
    EXPIRED_ORDER: "过期订单",
    WRONG_DEPARTMENT: "错部门",
    CONTRACT_REVOKED: "合同更正撤回",
    UNAUTHORIZED: "越权关联",
    REMOVED: "已剔除",
}

# 这些拒绝都属于「合同资格」层面，需要重新核合同
CONTRACT_REJECTIONS = {NO_CONTRACT, EXPIRED_ORDER, WRONG_DEPARTMENT, CONTRACT_REVOKED}


@dataclass(frozen=True)
class Contract:
    """供应商合同：归属公司、归属部门、有效期闭区间。"""

    contract_id: str
    company: str
    department: str
    supplier: str
    start_date: str
    end_date: str
    signed: bool = True
    note: str = ""

    def covers(self, company: str, department: str) -> bool:
        return self.company == company and self.department == department

    def date_status(self, trade_date: str) -> str:
        """返回 ``insufficient`` / ``expired`` / ``ok``。

        交易早于合同生效日视为合同尚未覆盖（属于合同资格不足），晚于结束日视为
        过期订单；结束日为闭区间，交易日等于结束日仍然有效。
        """
        if trade_date < self.start_date:
            return "insufficient"
        if trade_date > self.end_date:
            return "expired"
        return "ok"


@dataclass(frozen=True)
class ContractCorrection:
    """合同更正记录：带生效日，用于判断哪些原始关联应被撤回。

    ``effective_date`` 表示更正的生效日期；若它早于某笔交易日期，说明该交易在
    发生时就已落在更正后的口径下，原先按旧合同给出的资格要被撤回。
    """

    correction_id: str
    contract_id: str
    effective_date: str
    reason: str
    revoked_order_keys: tuple[str, ...] = ()
    replaced_end_date: str | None = None

    def revokes(self, order_key: str, trade_date: str) -> bool:
        if order_key not in self.revoked_order_keys:
            return False
        return self.effective_date < trade_date


@dataclass(frozen=True)
class ReconciliationLine:
    """一条待核验的业务明细。"""

    line_id: str
    order_key: str
    company: str
    department: str
    supplier: str
    amount: float
    trade_date: str
    contract_id: str = ""
    unauthorized: bool = False
    bill_id: str = ""
    removed: bool = False


@dataclass(frozen=True)
class Eligibility:
    """单行核验结论：可追溯、可解释。"""

    line_id: str
    order_key: str
    company: str
    department: str
    amount: float
    trade_date: str
    contract_id: str
    eligible: bool
    reason: str
    detail: str = ""

    @property
    def reason_label(self) -> str:
        return REJECTION_LABELS.get(self.reason, self.reason)

    def as_row(self) -> dict[str, Any]:
        return {
            "行号": self.line_id,
            "订单号": self.order_key,
            "公司": self.company,
            "部门": self.department,
            "金额": round(self.amount, 2),
            "交易日期": self.trade_date,
            "合同": self.contract_id or "—",
            "入账资格": "有" if self.eligible else "无",
            "拒绝原因": self.reason_label if not self.eligible else "—",
            "拒绝码": self.reason,
            "说明": self.detail,
        }


@dataclass
class LineCheck:
    """批量核验结果，附带可下钻的统计。"""

    period: str
    company: str
    lines: list[Eligibility] = field(default_factory=list)

    @property
    def eligible(self) -> list[Eligibility]:
        return [line for line in self.lines if line.eligible]

    @property
    def rejected(self) -> list[Eligibility]:
        return [line for line in self.lines if not line.eligible]

    @property
    def payable(self) -> float:
        return round(sum(line.amount for line in self.eligible), 2)

    def reason_counts(self) -> dict[str, int]:
        counts = {code: 0 for code in REJECTION_LABELS}
        for line in self.lines:
            counts[line.reason] = counts.get(line.reason, 0) + 1
        return counts


def period_bounds(period: str) -> tuple[str, str]:
    """把 ``2026-11`` 展开成闭区间首末日。"""
    try:
        year_s, month_s = period.split("-")
        year, month = int(year_s), int(month_s)
        start = date(year, month, 1)
    except (ValueError, AttributeError) as exc:  # 含 period 非字符串
        raise ValueError(f"对账期格式应为 YYYY-MM，收到 {period!r}") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"对账期月份非法：{period!r}")
    next_month = date(year + (month // 12), (month % 12) + 1, 1)
    end = date.fromordinal(next_month.toordinal() - 1)
    return start.isoformat(), end.isoformat()


def _pick_contract(
    line: ReconciliationLine,
    contracts: list[Contract],
) -> Contract | None:
    """为明细挑选合同：优先显式指定的合同，其次同公司同部门的合同。"""
    if line.contract_id:
        for contract in contracts:
            if contract.contract_id == line.contract_id:
                return contract
        return None
    candidates = [c for c in contracts if c.covers(line.company, line.department)]
    if not candidates:
        return None
    # 同样的公司+部门有多份合同时，取覆盖交易日的那一份，否则取最近生效的
    valid = [c for c in candidates if c.date_status(line.trade_date) != "expired"]
    pool = valid or candidates
    return sorted(pool, key=lambda c: c.start_date)[-1]


def evaluate_line(
    line: ReconciliationLine,
    *,
    contracts: list[Contract],
    corrections: list[ContractCorrection],
    period: str,
    report_company: str = "",
    report_department: str = "",
) -> Eligibility:
    """对单条明细给出入账资格结论。

    ``report_company`` / ``report_department`` 是本次报表部门。业务键关联会把它
    公司的行（例如同一订单号在乙公司的 25 元）也带进来，这类行在这里判为错部门，
    而不是悄悄计入应付。
    """
    def verdict(reason: str, detail: str = "", contract_id: str = "") -> Eligibility:
        return Eligibility(
            line_id=line.line_id,
            order_key=line.order_key,
            company=line.company,
            department=line.department,
            amount=line.amount,
            trade_date=line.trade_date,
            contract_id=contract_id,
            eligible=reason == ELIGIBLE,
            reason=reason,
            detail=detail,
        )

    start, end = period_bounds(period)
    if not start <= line.trade_date <= end:
        return verdict(
            OUT_OF_PERIOD,
            f"交易日期 {line.trade_date} 不在对账期 {start}~{end}",
        )

    if report_company and line.company != report_company:
        return verdict(
            WRONG_DEPARTMENT,
            f"明细归属公司为 {line.company}，报表公司为 {report_company}",
            line.contract_id,
        )
    if report_department and line.department != report_department:
        return verdict(
            WRONG_DEPARTMENT,
            f"明细归属部门为 {line.department}，报表部门为 {report_department}",
            line.contract_id,
        )

    if line.removed:
        return verdict(REMOVED, "该行已在账单上剔除", line.contract_id)

    contract = _pick_contract(line, contracts)
    if contract is None:
        return verdict(
            NO_CONTRACT,
            f"{line.company}/{line.department} 没有可用合同",
        )

    if contract.company != line.company or contract.department != line.department:
        # 显式指定合同但归属公司/部门不符：错部门，先于日期判断，方便运营定位
        return verdict(
            WRONG_DEPARTMENT,
            (
                f"合同归属为 {contract.company}/{contract.department}，"
                f"报表归属为 {line.company}/{line.department}"
            ),
            contract.contract_id,
        )

    date_status = contract.date_status(line.trade_date)
    if date_status == "expired":
        return verdict(
            EXPIRED_ORDER,
            f"交易日期 {line.trade_date} 晚于合同结束日 {contract.end_date}",
            contract.contract_id,
        )
    if date_status == "insufficient":
        return verdict(
            NO_CONTRACT,
            f"交易日期 {line.trade_date} 早于合同生效日 {contract.start_date}",
            contract.contract_id,
        )

    for correction in corrections:
        if correction.contract_id != contract.contract_id:
            continue
        if correction.revokes(line.order_key, line.trade_date):
            return verdict(
                CONTRACT_REVOKED,
                (
                    f"合同更正 {correction.correction_id} 于 {correction.effective_date} 生效，"
                    f"早于交易日期 {line.trade_date}，原资格已撤回（{correction.reason}）"
                ),
                contract.contract_id,
            )

    if line.unauthorized:
        return verdict(
            UNAUTHORIZED,
            "关联已被判定越权，签发前不得进入对外应付",
            contract.contract_id,
        )

    return verdict(ELIGIBLE, "合同、日期、部门均通过核验", contract.contract_id)


def evaluate_lines(
    lines: list[ReconciliationLine],
    *,
    contracts: list[Contract],
    corrections: list[ContractCorrection],
    period: str,
    company: str = "",
) -> LineCheck:
    """批量核验；``company`` 非空时只核验该公司的明细。"""
    selected = [line for line in lines if not company or line.company == company]
    check = LineCheck(period=period, company=company)
    for line in selected:
        check.lines.append(
            evaluate_line(
                line,
                contracts=contracts,
                corrections=corrections,
                period=period,
            )
        )
    return check
