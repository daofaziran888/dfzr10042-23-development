"""供应商归属对账服务：归属对账批次、账单流转、入账账本与应付月报。

与 ``app.domain.supplier_recon`` 的分工：
领域模块只做纯核验，本服务负责有状态的部分——批次、账单、账本、报告以及并发控制。
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.supplier_recon import (
    ELIGIBLE,
    REJECTION_LABELS,
    REMOVED,
    Contract,
    ContractCorrection,
    Eligibility,
    ReconciliationLine,
    evaluate_line,
    period_bounds,
)

__all__ = ["SupplierReconService", "recon_service", "REPORT_DEPARTMENT"]

BILL_PENDING = "待领取"
BILL_CLAIMED = "已领取"
BILL_RETURNED = "已退回复核"
BILL_SIGNED = "已签发"

# 各公司的报表部门：归属核验以报表部门为准，别部门的行必须显式拒绝。
REPORT_DEPARTMENT = {"甲公司": "采购部", "乙公司": "销售部"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _money(value: float) -> float:
    return round(float(value), 2)


@dataclass
class ReconBatch:
    batch_id: int
    batch_no: str
    period: str
    company: str
    department: str
    created_at: str
    line_ids: list[str]
    checks: list[Eligibility] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    bill_id: int = 0
    signed_payable: float | None = None

    def refresh(self, checks: list[Eligibility]) -> None:
        self.checks = checks
        self.events.append(
            {
                "at": _now(),
                "event": "重新核验",
                "eligible": sum(1 for c in checks if c.eligible),
                "rejected": sum(1 for c in checks if not c.eligible),
            }
        )

    @property
    def eligible(self) -> list[Eligibility]:
        return [c for c in self.checks if c.eligible]

    @property
    def current_payable(self) -> float:
        """按当前合同/更正/越权状态重算的金额，可能已被撤回。"""
        return _money(sum(c.amount for c in self.eligible))

    @property
    def payable(self) -> float:
        """批次应付：签发后冻结为签发时的金额，保证历史口径可复算。"""
        if self.signed_payable is not None:
            return self.signed_payable
        return self.current_payable

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for check in self.checks:
            counts[check.reason] = counts.get(check.reason, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_no": self.batch_no,
            "period": self.period,
            "company": self.company,
            "department": self.department,
            "created_at": self.created_at,
            "status": self.status,
            "bill_id": self.bill_id,
            "payable": self.payable,
            "signed_payable": self.signed_payable,
            "current_payable": self.current_payable,
            "eligible_count": len(self.eligible),
            "rejected_count": len(self.checks) - len(self.eligible),
            "reason_counts": self.reason_counts(),
            "checks": [check.as_row() for check in self.checks],
            "events": list(self.events),
        }

    @property
    def status(self) -> str:
        if self.bill_id == 0:
            return "已生成"
        return "见账单"


@dataclass
class Bill:
    bill_id: int
    bill_no: str
    batch_id: int
    period: str
    company: str
    department: str
    created_at: str
    status: str = BILL_PENDING
    line_ids: list[str] = field(default_factory=list)
    removed_line_ids: list[str] = field(default_factory=list)
    claimed_by: str = ""
    claimed_at: str = ""
    returned_reason: str = ""
    returned_at: str = ""
    signed_at: str = ""
    signed_by: str = ""
    signed_amount: float = 0.0
    signed_lines: list[dict[str, Any]] = field(default_factory=list)
    sign_attempts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def log(self, event: str, **extra: Any) -> None:
        self.events.append({"at": _now(), "event": event, **extra})

    def as_dict(self) -> dict[str, Any]:
        return {
            "bill_id": self.bill_id,
            "bill_no": self.bill_no,
            "batch_id": self.batch_id,
            "period": self.period,
            "company": self.company,
            "department": self.department,
            "status": self.status,
            "line_ids": list(self.line_ids),
            "removed_line_ids": list(self.removed_line_ids),
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "returned_reason": self.returned_reason,
            "signed_at": self.signed_at,
            "signed_by": self.signed_by,
            "signed_amount": self.signed_amount,
            "signed_lines": list(self.signed_lines),
            "sign_attempts": list(self.sign_attempts),
            "events": list(self.events),
        }


@dataclass
class LedgerEntry:
    entry_id: int
    bill_id: int
    bill_no: str
    period: str
    company: str
    department: str
    amount: float
    signed_at: str
    signed_by: str
    basis: list[dict[str, Any]]
    basis_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "bill_id": self.bill_id,
            "bill_no": self.bill_no,
            "period": self.period,
            "company": self.company,
            "department": self.department,
            "amount": self.amount,
            "signed_at": self.signed_at,
            "signed_by": self.signed_by,
            "basis_hash": self.basis_hash,
            "basis": list(self.basis),
        }


def _basis_hash(basis: list[dict[str, Any]]) -> str:
    payload = "|".join(
        f"{row['行号']}:{row['订单号']}:{row['合同']}:{row['金额']}" for row in basis
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class SupplierReconService:
    """内存版归属对账服务；构造时注入演示数据，测试可另建实例。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.contracts: list[Contract] = []
        self.corrections: list[ContractCorrection] = []
        self.lines: list[ReconciliationLine] = []
        self.batches: dict[int, ReconBatch] = {}
        self.bills: dict[int, Bill] = {}
        self.ledger: list[LedgerEntry] = []
        self.reports: dict[int, dict[str, Any]] = {}
        self._batch_seq = 0
        self._bill_seq = 0
        self._entry_seq = 0
        self._report_seq = 0
        self._seed()

    # ------------------------------------------------------------------
    # 演示数据
    # ------------------------------------------------------------------
    def _seed(self) -> None:
        self.contracts = [
            Contract(
                contract_id="HT-JIA-2026",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                start_date="2026-11-01",
                end_date="2026-12-05",
                note="跨月有效：11 月对账单只计 11-01~11-30 内订单",
            ),
            Contract(
                contract_id="HT-JIA-LEGACY",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                start_date="2026-09-01",
                end_date="2026-11-08",
                note="旧合同，11-08 后订单不再覆盖",
            ),
            Contract(
                contract_id="HT-YI-2026",
                company="乙公司",
                department="销售部",
                supplier="华东电子",
                start_date="2026-10-01",
                end_date="2026-12-31",
                note="归属销售部，采购部报表不得引用",
            ),
        ]
        # 仅按业务键关联时的原始明细：同一订单号在甲、乙各有一条。
        self.lines = [
            ReconciliationLine(
                line_id="L-0001",
                order_key="SO-20261101",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                amount=60.00,
                trade_date="2026-11-18",
                contract_id="HT-JIA-2026",
                bill_id="B-JIA-11",
            ),
            ReconciliationLine(
                line_id="L-0002",
                order_key="SO-20261101",
                company="乙公司",
                department="采购部",
                supplier="华东电子",
                amount=25.00,
                trade_date="2026-11-18",
                contract_id="HT-YI-2026",
                bill_id="B-YI-11",
            ),
            ReconciliationLine(
                line_id="L-0003",
                order_key="SO-20261115",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                amount=88.00,
                trade_date="2026-11-15",
                contract_id="HT-JIA-LEGACY",
                bill_id="B-JIA-11",
            ),
            ReconciliationLine(
                line_id="L-0004",
                order_key="SO-20261120",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                amount=15.00,
                trade_date="2026-11-20",
                contract_id="HT-JIA-2026",
                bill_id="B-JIA-11",
            ),
            ReconciliationLine(
                line_id="L-0005",
                order_key="SO-20261125",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                amount=40.00,
                trade_date="2026-11-25",
                contract_id="HT-JIA-2026",
                unauthorized=True,
                bill_id="B-JIA-11",
            ),
            ReconciliationLine(
                line_id="L-0006",
                order_key="SO-20261201",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                amount=70.00,
                trade_date="2026-12-01",
                contract_id="HT-JIA-2026",
                bill_id="B-JIA-12",
            ),
            ReconciliationLine(
                line_id="L-0007",
                order_key="SO-20261102",
                company="乙公司",
                department="销售部",
                supplier="华东电子",
                amount=45.00,
                trade_date="2026-11-10",
                contract_id="HT-YI-2026",
                bill_id="B-YI-11",
            ),
            ReconciliationLine(
                line_id="L-0008",
                order_key="SO-20261103",
                company="乙公司",
                department="销售部",
                supplier="华东电子",
                amount=30.00,
                trade_date="2026-11-12",
                contract_id="HT-YI-2026",
                bill_id="B-YI-11",
            ),
        ]

    # ------------------------------------------------------------------
    # 查询：合同、更正经、关联入口
    # ------------------------------------------------------------------
    def list_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "contract_id": c.contract_id,
                "company": c.company,
                "department": c.department,
                "supplier": c.supplier,
                "start_date": c.start_date,
                "end_date": c.end_date,
                "signed": c.signed,
                "note": c.note,
            }
            for c in self.contracts
        ]

    def list_corrections(self) -> list[dict[str, Any]]:
        return [
            {
                "correction_id": c.correction_id,
                "contract_id": c.contract_id,
                "effective_date": c.effective_date,
                "reason": c.reason,
                "revoked_order_keys": list(c.revoked_order_keys),
                "replaced_end_date": c.replaced_end_date,
            }
            for c in self.corrections
        ]

    def association_view(
        self,
        *,
        period: str = "2026-11",
        company: str = "",
        order_key: str = "",
    ) -> dict[str, Any]:
        """关联入口：按业务键分组展示两家公司、合同有效期与拒绝原因。"""
        start, end = period_bounds(period)
        selected = [
            line
            for line in self.lines
            if (not company or line.company == company or self._shares_order(line, company))
            and (not order_key or line.order_key == order_key)
        ]
        # 指定公司时，以该公司的报表部门为视角：同一订单号在别家公司的行会被
        # 业务键关联带进来，应当判为错部门而不是合格。
        report_department = self.report_department(company) if company else ""
        grouped: dict[str, list[dict[str, Any]]] = {}
        reason_counts = {code: 0 for code in REJECTION_LABELS}
        eligible_amount = 0.0
        for line in selected:
            check = evaluate_line(
                line,
                contracts=self.contracts,
                corrections=self.corrections,
                period=period,
                report_company=company,
                report_department=report_department,
            )
            contract = next(
                (c for c in self.contracts if c.contract_id == check.contract_id),
                None,
            )
            reason_counts[check.reason] = reason_counts.get(check.reason, 0) + 1
            if check.eligible:
                eligible_amount += check.amount
            row = check.as_row()
            row.update(
                {
                    "合同开始": contract.start_date if contract else "—",
                    "合同结束": contract.end_date if contract else "—",
                    "合同归属": (
                        f"{contract.company}/{contract.department}" if contract else "—"
                    ),
                    "日期在有效期内": bool(
                        contract and contract.date_status(line.trade_date) == "ok"
                    ),
                    "对账期内": start <= line.trade_date <= end,
                }
            )
            grouped.setdefault(line.order_key, []).append(row)

        orders = [
            {
                "order_key": key,
                "companies": sorted({row["公司"] for row in rows}),
                "line_count": len(rows),
                "eligible_amount": _money(
                    sum(row["金额"] for row in rows if row["入账资格"] == "有")
                ),
                "rows": rows,
            }
            for key, rows in sorted(grouped.items())
        ]
        return {
            "entry": "dataset_join",
            "period": period,
            "date_bounds": {"start": start, "end": end},
            "company": company,
            "companies": sorted({line.company for line in self.lines}),
            "orders": orders,
            "summary": {
                "line_count": len(selected),
                "order_count": len(orders),
                "eligible_count": sum(
                    1
                    for order in orders
                    for row in order["rows"]
                    if row["入账资格"] == "有"
                ),
                "eligible_amount": _money(eligible_amount),
                "reason_counts": reason_counts,
                "reason_labels": REJECTION_LABELS,
            },
        }

    # ------------------------------------------------------------------
    # 归属对账批次
    # ------------------------------------------------------------------
    def _shares_order(self, line: ReconciliationLine, company: str) -> bool:
        """该行是否与指定公司的某条明细共享业务键（会被业务键关联带进来）。"""
        return any(
            other.company == company and other.order_key == line.order_key
            for other in self.lines
        )

    def _company_lines(self, company: str) -> list[ReconciliationLine]:
        # 业务键关联会把同一订单号在别家公司的行也带进来。归属批次必须把这些行
        # 一起收进来核验，才能把「错部门」和「过期订单」区分开，而不是先按公司
        # 过滤掉、让错部门的行凭空消失。
        own = [line for line in self.lines if line.company == company]
        own_keys = {line.order_key for line in own}
        pulled_in = [
            line
            for line in self.lines
            if line.company != company and line.order_key in own_keys
        ]
        return own + pulled_in

    def _evaluate_batch_lines(
        self,
        line_ids: list[str],
        period: str,
        company: str,
        department: str,
    ) -> list[Eligibility]:
        by_id = {line.line_id: line for line in self.lines}
        checks: list[Eligibility] = []
        for line_id in line_ids:
            line = by_id.get(line_id)
            if line is None:
                continue
            checks.append(
                evaluate_line(
                    line,
                    contracts=self.contracts,
                    corrections=self.corrections,
                    period=period,
                    report_company=company,
                    report_department=department,
                )
            )
        return checks

    def report_department(self, company: str) -> str:
        return REPORT_DEPARTMENT.get(company, "")

    def create_batch(
        self, *, period: str = "2026-11", company: str = "甲公司"
    ) -> dict[str, Any]:
        """生成归属对账批次与待签账单；同周期同公司已存在时刷新后复用。"""
        with self._lock:
            period_bounds(period)  # 校验格式
            existing = next(
                (
                    batch
                    for batch in self.batches.values()
                    if batch.period == period and batch.company == company
                ),
                None,
            )
            if existing is not None:
                checks = self._evaluate_batch_lines(
                    existing.line_ids,
                    period,
                    existing.company,
                    existing.department,
                )
                existing.refresh(checks)
                self._sync_bill_lines(existing)
                return {
                    "created": False,
                    "message": "该周期该公司的归属对账批次已存在，已按最新合同口径重新核验",
                    "batch": existing.as_dict(),
                    "bill": self.bills[existing.bill_id].as_dict()
                    if existing.bill_id in self.bills
                    else None,
                }

            lines = self._company_lines(company)
            if not lines:
                raise ValueError(f"{company} 在 {period} 没有可对账的供应商明细")
            line_ids = [line.line_id for line in lines]
            department = self.report_department(company)
            checks = self._evaluate_batch_lines(line_ids, period, company, department)
            self._batch_seq += 1
            batch = ReconBatch(
                batch_id=self._batch_seq,
                batch_no=f"GZ-{period}-{company}-{self._batch_seq:03d}",
                period=period,
                company=company,
                department=department,
                created_at=_now(),
                line_ids=line_ids,
                checks=checks,
            )
            batch.events.append(
                {
                    "at": _now(),
                    "event": "创建归属对账批次",
                    "line_count": len(line_ids),
                    "eligible_count": len(batch.eligible),
                    "payable": batch.payable,
                }
            )
            self._bill_seq += 1
            bill = Bill(
                bill_id=self._bill_seq,
                bill_no=f"ZD-{period}-{company}-{self._bill_seq:03d}",
                batch_id=batch.batch_id,
                period=period,
                company=company,
                department=batch.department,
                created_at=_now(),
                line_ids=[check.line_id for check in batch.eligible],
            )
            bill.log("生成待签账单", payable=batch.payable)
            self.bills[bill.bill_id] = bill
            batch.bill_id = bill.bill_id
            self.batches[batch.batch_id] = batch
            return {
                "created": True,
                "message": f"已生成{company} {period} 归属对账批次",
                "batch": batch.as_dict(),
                "bill": bill.as_dict(),
            }

    def list_batches(self, *, period: str = "", company: str = "") -> list[dict[str, Any]]:
        return [
            batch.as_dict()
            for batch in sorted(self.batches.values(), key=lambda b: b.batch_id)
            if (not period or batch.period == period)
            and (not company or batch.company == company)
        ]

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        batch = self.batches.get(batch_id)
        if batch is None:
            return None
        data = batch.as_dict()
        if batch.bill_id in self.bills:
            data["bill"] = self.bills[batch.bill_id].as_dict()
        return data

    def _sync_bill_lines(self, batch: ReconBatch) -> None:
        """批次重算后，把新出现的合格行补进未签发账单。"""
        bill = self.bills.get(batch.bill_id)
        if bill is None or bill.status == BILL_SIGNED:
            return
        eligible_ids = [check.line_id for check in batch.eligible]
        new_ids = [
            line_id
            for line_id in eligible_ids
            if line_id not in bill.line_ids and line_id not in bill.removed_line_ids
        ]
        if new_ids:
            bill.line_ids.extend(new_ids)
            bill.log("批次重算补入合格行", line_ids=new_ids)

    # ------------------------------------------------------------------
    # 账单流转
    # ------------------------------------------------------------------
    def list_bills(self, *, period: str = "", company: str = "") -> list[dict[str, Any]]:
        return [
            self._bill_view(bill)
            for bill in sorted(self.bills.values(), key=lambda b: b.bill_id)
            if (not period or bill.period == period)
            and (not company or bill.company == company)
        ]

    def _bill_checks(self, bill: Bill) -> list[Eligibility]:
        by_id = {line.line_id: line for line in self.lines}
        checks: list[Eligibility] = []
        for line_id in bill.line_ids:
            line = by_id.get(line_id)
            if line is None:
                continue
            if line_id in bill.removed_line_ids:
                checks.append(
                    Eligibility(
                        line_id=line.line_id,
                        order_key=line.order_key,
                        company=line.company,
                        department=line.department,
                        amount=line.amount,
                        trade_date=line.trade_date,
                        contract_id=line.contract_id,
                        eligible=False,
                        reason=REMOVED,
                        detail="该行已在账单上剔除",
                    )
                )
            else:
                checks.append(
                    evaluate_line(
                        line,
                        contracts=self.contracts,
                        corrections=self.corrections,
                        period=bill.period,
                        report_company=bill.company,
                        report_department=bill.department,
                    )
                )
        return checks

    def _bill_view(self, bill: Bill) -> dict[str, Any]:
        checks = self._bill_checks(bill)
        active = [c for c in checks if c.line_id not in bill.removed_line_ids]
        blockers = [c for c in active if not c.eligible]
        data = bill.as_dict()
        data.update(
            {
                "checks": [c.as_row() for c in checks],
                "payable": _money(sum(c.amount for c in active if c.eligible)),
                "blockers": [
                    {
                        "行号": c.line_id,
                        "订单号": c.order_key,
                        "金额": c.amount,
                        "拒绝原因": c.reason_label,
                        "拒绝码": c.reason,
                    }
                    for c in blockers
                ],
                "can_sign": bill.status == BILL_CLAIMED and not blockers,
                "booked": any(entry.bill_id == bill.bill_id for entry in self.ledger),
            }
        )
        return data

    def get_bill(self, bill_id: int) -> dict[str, Any] | None:
        bill = self.bills.get(bill_id)
        return self._bill_view(bill) if bill else None

    def claim_bill(self, bill_id: int, *, actor: str = "财务") -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            bill = self.bills.get(bill_id)
            if bill is None:
                return False, f"账单 {bill_id} 不存在", None
            if bill.status == BILL_SIGNED:
                return False, "账单已签发，不能重复领取", self._bill_view(bill)
            if bill.status == BILL_CLAIMED:
                return False, "账单已被领取，请勿重复领取", self._bill_view(bill)
            bill.status = BILL_CLAIMED
            bill.claimed_by = actor
            bill.claimed_at = _now()
            bill.log("领取账单", actor=actor)
            return True, f"{bill.bill_no} 已领取，待确认后签发", self._bill_view(bill)

    def remove_line(
        self, bill_id: int, line_id: str, *, actor: str = "财务", reason: str = ""
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            bill = self.bills.get(bill_id)
            if bill is None:
                return False, f"账单 {bill_id} 不存在", None
            if bill.status != BILL_CLAIMED:
                return False, "只有已领取的账单可以剔除明细", self._bill_view(bill)
            if line_id not in bill.line_ids:
                return False, f"行 {line_id} 不在账单 {bill.bill_no} 上", self._bill_view(bill)
            if line_id in bill.removed_line_ids:
                return False, f"行 {line_id} 已经剔除", self._bill_view(bill)
            bill.removed_line_ids.append(line_id)
            bill.log("剔除明细", actor=actor, line_id=line_id, reason=reason)
            batch = self.batches.get(bill.batch_id)
            if batch is not None:
                batch.events.append(
                    {
                        "at": _now(),
                        "event": "账单剔除明细",
                        "line_id": line_id,
                        "reason": reason,
                    }
                )
            return True, f"已从 {bill.bill_no} 剔除行 {line_id}", self._bill_view(bill)

    def return_bill(
        self, bill_id: int, *, actor: str = "财务", reason: str = "退回复核"
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            bill = self.bills.get(bill_id)
            if bill is None:
                return False, f"账单 {bill_id} 不存在", None
            if bill.status == BILL_SIGNED:
                return False, "账单已签发，不能退回", self._bill_view(bill)
            if bill.status != BILL_CLAIMED:
                return False, "只有已领取的账单可以退回复核", self._bill_view(bill)
            bill.status = BILL_RETURNED
            bill.returned_reason = reason
            bill.returned_at = _now()
            bill.log("退回复核", actor=actor, reason=reason)
            batch = self.batches.get(bill.batch_id)
            if batch is not None:
                batch.events.append(
                    {"at": _now(), "event": "账单退回复核", "reason": reason}
                )
            return (
                True,
                f"{bill.bill_no} 已退回复核；重新领取前本期尚未入账",
                self._bill_view(bill),
            )

    def sign_bill(
        self, bill_id: int, *, actor: str = "财务"
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """签发账单并入账。

        并发保护：进程锁 + 账本按 bill_id 的唯一约束，同一账单只会写一条入账记录。
        签发前按当前合同/更正/越权状态重新核验，任何非合格行都必须先剔除。
        """
        with self._lock:
            bill = self.bills.get(bill_id)
            if bill is None:
                return False, f"账单 {bill_id} 不存在", None
            existing = next((e for e in self.ledger if e.bill_id == bill_id), None)
            if existing is not None:
                return (
                    True,
                    f"{bill.bill_no} 已签发入账，本次未重复入账",
                    {"entry": existing.as_dict(), "duplicate": True, "bill": self._bill_view(bill)},
                )
            if bill.status == BILL_SIGNED:
                return (
                    True,
                    f"{bill.bill_no} 已签发入账，本次未重复入账",
                    {"duplicate": True, "bill": self._bill_view(bill)},
                )
            if bill.status != BILL_CLAIMED:
                return False, "只有已领取的账单可以签发", self._bill_view(bill)

            checks = self._bill_checks(bill)
            active = [c for c in checks if c.line_id not in bill.removed_line_ids]
            blockers = [c for c in active if not c.eligible]
            if blockers:
                reasons = "、".join(
                    f"{c.line_id}({c.reason_label})" for c in blockers
                )
                bill.sign_attempts.append(
                    {
                        "at": _now(),
                        "actor": actor,
                        "result": "拒绝签发",
                        "reasons": [
                            {"行号": c.line_id, "拒绝码": c.reason, "拒绝原因": c.reason_label}
                            for c in blockers
                        ],
                    }
                )
                bill.log("签发被拒绝", reasons=reasons)
                return (
                    False,
                    f"存在未剔除的不合格行，不能签发：{reasons}",
                    self._bill_view(bill),
                )

            eligible = [c for c in active if c.eligible]
            if not eligible:
                bill.sign_attempts.append(
                    {"at": _now(), "actor": actor, "result": "拒绝签发", "reasons": "无合格行"}
                )
                return False, "账单没有可入账的合格行", self._bill_view(bill)

            basis = [
                {
                    "行号": c.line_id,
                    "订单号": c.order_key,
                    "合同": c.contract_id,
                    "金额": _money(c.amount),
                    "交易日期": c.trade_date,
                    "拒绝原因": "—",
                }
                for c in eligible
            ]
            amount = _money(sum(c.amount for c in eligible))
            self._entry_seq += 1
            entry = LedgerEntry(
                entry_id=self._entry_seq,
                bill_id=bill.bill_id,
                bill_no=bill.bill_no,
                period=bill.period,
                company=bill.company,
                department=bill.department,
                amount=amount,
                signed_at=_now(),
                signed_by=actor,
                basis=basis,
                basis_hash=_basis_hash(basis),
            )
            self.ledger.append(entry)
            bill.status = BILL_SIGNED
            bill.signed_at = entry.signed_at
            bill.signed_by = actor
            bill.signed_amount = amount
            bill.signed_lines = basis
            bill.log("签发入账", amount=amount, entry_id=entry.entry_id)
            batch = self.batches.get(bill.batch_id)
            if batch is not None:
                batch.signed_payable = amount
                batch.events.append(
                    {
                        "at": _now(),
                        "event": "账单签发入账",
                        "entry_id": entry.entry_id,
                        "amount": amount,
                    }
                )
            return (
                True,
                f"{bill.bill_no} 已签发，入账 {amount:.2f} 元",
                {"entry": entry.as_dict(), "bill": self._bill_view(bill)},
            )

    # ------------------------------------------------------------------
    # 合同更正与越权标记
    # ------------------------------------------------------------------
    def apply_correction(
        self,
        *,
        contract_id: str,
        effective_date: str,
        reason: str,
        revoked_order_keys: list[str],
        replaced_end_date: str | None = None,
        correction_id: str = "",
    ) -> dict[str, Any]:
        """登记合同更正并回溯刷新未签发批次；生效日早于交易的资格被撤回。"""
        with self._lock:
            contract = next(
                (c for c in self.contracts if c.contract_id == contract_id), None
            )
            if contract is None:
                raise ValueError(f"合同 {contract_id} 不存在")
            period_bounds(effective_date[:7])
            if not correction_id:
                correction_id = f"CORR-{len(self.corrections) + 1:03d}"
            correction = ContractCorrection(
                correction_id=correction_id,
                contract_id=contract_id,
                effective_date=effective_date,
                reason=reason,
                revoked_order_keys=tuple(revoked_order_keys),
                replaced_end_date=replaced_end_date,
            )
            self.corrections.append(correction)
            if replaced_end_date:
                replaced = [
                    Contract(
                        **{
                            **c.__dict__,
                            "end_date": replaced_end_date,
                        }
                    )
                    if c.contract_id == contract_id
                    else c
                    for c in self.contracts
                ]
                self.contracts = replaced

            refreshed: list[dict[str, Any]] = []
            for batch in sorted(self.batches.values(), key=lambda b: b.batch_id):
                affected = any(
                    check.contract_id == contract_id for check in batch.checks
                ) or any(
                    line_id in {line.line_id for line in self.lines if line.contract_id == contract_id}
                    for line_id in batch.line_ids
                )
                if not affected:
                    continue
                checks = self._evaluate_batch_lines(
                    batch.line_ids, batch.period, batch.company, batch.department
                )
                batch.refresh(checks)
                batch.events.append(
                    {
                        "at": _now(),
                        "event": "合同更正回溯",
                        "correction_id": correction.correction_id,
                        "effective_date": effective_date,
                    }
                )
                self._sync_bill_lines(batch)
                revoked = [
                    c.line_id
                    for c in checks
                    if c.reason == "CONTRACT_REVOKED"
                ]
                bill = self.bills.get(batch.bill_id)
                unsigned = bill is not None and bill.status != BILL_SIGNED
                refreshed.append(
                    {
                        "batch_no": batch.batch_no,
                        "payable": batch.payable,
                        "current_payable": batch.current_payable,
                        "bill_status": bill.status if bill else None,
                        "revoked_line_ids": revoked,
                        # 已签发账单不回改历史：当前重算与冻结口径的差异单独列出，供复核。
                        "post_sign_drift": (
                            _money(batch.current_payable - batch.signed_payable)
                            if not unsigned and batch.signed_payable is not None
                            else None
                        ),
                    }
                )
            return {
                "correction": {
                    "correction_id": correction.correction_id,
                    "contract_id": contract_id,
                    "effective_date": effective_date,
                    "reason": reason,
                    "revoked_order_keys": list(revoked_order_keys),
                },
                "refreshed_batches": refreshed,
            }

    def mark_unauthorized(
        self, line_id: str, *, reason: str = "关联核验判定越权"
    ) -> dict[str, Any]:
        """在签发前把一条关联标记为越权；已存在的未签发批次同步刷新。"""
        with self._lock:
            line = next((line for line in self.lines if line.line_id == line_id), None)
            if line is None:
                raise ValueError(f"明细 {line_id} 不存在")
            if line.unauthorized:
                return {"line_id": line_id, "changed": False, "reason": reason}
            self.lines = [
                ReconciliationLine(
                    **{**line.__dict__, "unauthorized": True}
                )
                if line.line_id == line_id
                else line
                for line in self.lines
            ]
            refreshed = []
            for batch in self.batches.values():
                if line_id in batch.line_ids:
                    batch.refresh(
                        self._evaluate_batch_lines(
                            batch.line_ids, batch.period, batch.company, batch.department
                        )
                    )
                    batch.events.append(
                        {
                            "at": _now(),
                            "event": "关联判定越权",
                            "line_id": line_id,
                            "reason": reason,
                        }
                    )
                    refreshed.append(batch.batch_no)
            return {
                "line_id": line_id,
                "changed": True,
                "reason": reason,
                "refreshed_batches": refreshed,
            }

    # ------------------------------------------------------------------
    # 入账账本与月报
    # ------------------------------------------------------------------
    def list_ledger(self, *, period: str = "") -> list[dict[str, Any]]:
        return [
            entry.as_dict()
            for entry in self.ledger
            if not period or entry.period == period
        ]

    def recompute_entry(self, entry_id: int) -> dict[str, Any] | None:
        """按签发时冻结的口径复算入账金额，验证历史口径未被现在数据改写。"""
        entry = next((e for e in self.ledger if e.entry_id == entry_id), None)
        if entry is None:
            return None
        recomputed = _money(sum(row["金额"] for row in entry.basis))
        return {
            "entry_id": entry.entry_id,
            "bill_no": entry.bill_no,
            "basis_hash": entry.basis_hash,
            "signed_amount": entry.amount,
            "recomputed_amount": recomputed,
            "matches": abs(recomputed - entry.amount) < 0.005,
            "current_bill_payable": (
                self._bill_view(self.bills[entry.bill_id])["payable"]
                if entry.bill_id in self.bills
                else None
            ),
        }

    def generate_report(self, *, period: str = "2026-11") -> dict[str, Any]:
        """从归属对账批次生成应付月报，并核对实际入账与未签发记录。"""
        with self._lock:
            start, end = period_bounds(period)
            batches = [
                batch
                for batch in self.batches.values()
                if batch.period == period
            ]
            bills = [bill for bill in self.bills.values() if bill.period == period]

            receipts: dict[tuple[str, str], dict[str, Any]] = {}
            payable_total = 0.0
            booked_total = 0.0

            for batch in batches:
                key = (batch.company, batch.department)
                item = receipts.setdefault(
                    key,
                    {
                        "company": key[0],
                        "department": key[1],
                        "payable": 0.0,
                        "booked": 0.0,
                        "unbooked": 0.0,
                        "batch_nos": [],
                    },
                )
                item["payable"] = _money(item["payable"] + batch.payable)
                item["batch_nos"].append(batch.batch_no)
                payable_total += batch.payable

            for entry in self.ledger:
                if entry.period != period:
                    continue
                key = (entry.company, entry.department)
                item = receipts.setdefault(
                    key,
                    {
                        "company": key[0],
                        "department": key[1],
                        "payable": 0.0,
                        "booked": 0.0,
                        "unbooked": 0.0,
                        "batch_nos": [],
                    },
                )
                item["booked"] = _money(item["booked"] + entry.amount)
                booked_total += entry.amount

            for item in receipts.values():
                item["unbooked"] = _money(item["payable"] - item["booked"])

            unsigned: list[dict[str, Any]] = []
            for bill in bills:
                if bill.status == BILL_SIGNED:
                    continue
                view = self._bill_view(bill)
                unsigned.append(
                    {
                        "bill_no": bill.bill_no,
                        "company": bill.company,
                        "department": bill.department,
                        "status": bill.status,
                        "payable": view["payable"],
                        "blockers": view["blockers"],
                        "last_attempt": (bill.sign_attempts[-1] if bill.sign_attempts else None),
                    }
                )

            breaches = [
                record
                for bill in bills
                for record in bill.sign_attempts
                if record.get("result") == "拒绝签发"
            ]

            self._report_seq += 1
            report = {
                "report_id": self._report_seq,
                "report_no": f"RP-{period}-{self._report_seq:03d}",
                "template": "应付对账月报",
                "period": period,
                "date_bounds": {"start": start, "end": end},
                "generated_at": _now(),
                "batch_count": len(batches),
                "batches": [
                    {
                        "batch_no": batch.batch_no,
                        "company": batch.company,
                        "department": batch.department,
                        "payable": batch.payable,
                        "eligible_count": len(batch.eligible),
                        "rejected_count": len(batch.checks) - len(batch.eligible),
                        "reason_counts": batch.reason_counts(),
                    }
                    for batch in batches
                ],
                "batch_lines": [
                    {
                        "batch_no": batch.batch_no,
                        "company": batch.company,
                        "department": batch.department,
                        **row,
                    }
                    for batch in batches
                    for row in (check.as_row() for check in batch.checks)
                ],
                "department_receipts": sorted(
                    receipts.values(),
                    key=lambda item: (item["company"], item["department"]),
                ),
                "booked_entries": [
                    entry.as_dict() for entry in self.ledger if entry.period == period
                ],
                "unsigned_bills": unsigned,
                "sign_rejections": breaches,
                "totals": {
                    "payable": _money(payable_total),
                    "booked": _money(booked_total),
                    "unbooked": _money(payable_total - booked_total),
                    "batch_count": len(batches),
                    "bill_count": len(bills),
                    "signed_bill_count": len(bills) - len(unsigned),
                    "unsigned_bill_count": len(unsigned),
                },
            }
            self.reports[report["report_id"]] = report
            return report

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        return self.reports.get(report_id)

    def list_reports(self) -> list[dict[str, Any]]:
        return [
            {
                "report_id": report["report_id"],
                "report_no": report["report_no"],
                "period": report["period"],
                "generated_at": report["generated_at"],
                "totals": report["totals"],
            }
            for report in sorted(self.reports.values(), key=lambda r: r["report_id"])
        ]


recon_service = SupplierReconService()
