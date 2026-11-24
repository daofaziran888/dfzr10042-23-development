"""供应商归属对账接口：关联入口、归属批次、账单流转、账本与应付月报。

路由只做参数解析和错误翻译，核验、状态流转和并发控制全部在服务层完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.supplier_recon import recon_service

router = APIRouter(prefix="/api/recon", tags=["供应商归属对账"])


class BatchPayload(BaseModel):
    period: str = "2026-11"
    company: str = "甲公司"


class RemoveLinePayload(BaseModel):
    line_id: str = Field(min_length=1)
    actor: str = "财务"
    reason: str = ""


class ReturnPayload(BaseModel):
    actor: str = "财务"
    reason: str = "退回复核"


class ClaimPayload(BaseModel):
    actor: str = "财务"


class SignPayload(BaseModel):
    actor: str = "财务"


class CorrectionPayload(BaseModel):
    contract_id: str = Field(min_length=1)
    effective_date: str = Field(min_length=10)
    reason: str = ""
    revoked_order_keys: list[str] = Field(default_factory=list)
    replaced_end_date: str | None = None
    correction_id: str = ""


class UnauthorizedPayload(BaseModel):
    line_id: str = Field(min_length=1)
    reason: str = "关联核验判定越权"


class ReportPayload(BaseModel):
    period: str = "2026-11"


# ---------------------------------------------------------------- 关联入口
@router.get("/contracts")
def list_contracts() -> dict[str, Any]:
    return {"items": recon_service.list_contracts()}


@router.get("/corrections")
def list_corrections() -> dict[str, Any]:
    return {"items": recon_service.list_corrections()}


@router.get("/association")
def association_view(
    period: str = Query(default="2026-11"),
    company: str = Query(default=""),
    order_key: str = Query(default=""),
) -> dict[str, Any]:
    """从关联入口查两公司、日期边界和拒绝原因。"""
    try:
        return recon_service.association_view(
            period=period, company=company, order_key=order_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------- 归属对账批次
@router.get("/batches")
def list_batches(
    period: str = Query(default=""), company: str = Query(default="")
) -> dict[str, Any]:
    return {"items": recon_service.list_batches(period=period, company=company)}


@router.post("/batches")
def create_batch(payload: BatchPayload) -> dict[str, Any]:
    """生成归属对账批次和待签账单；已存在则按最新合同口径重新核验。"""
    try:
        return recon_service.create_batch(period=payload.period, company=payload.company)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    batch = recon_service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"归属对账批次 {batch_id} 不存在")
    return batch


# ---------------------------------------------------------------- 账单流转
@router.get("/bills")
def list_bills(
    period: str = Query(default=""), company: str = Query(default="")
) -> dict[str, Any]:
    return {"items": recon_service.list_bills(period=period, company=company)}


@router.get("/bills/{bill_id}")
def get_bill(bill_id: int) -> dict[str, Any]:
    bill = recon_service.get_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail=f"账单 {bill_id} 不存在")
    return bill


@router.post("/bills/{bill_id}/claim")
def claim_bill(bill_id: int, payload: ClaimPayload) -> dict[str, Any]:
    ok, message, bill = recon_service.claim_bill(bill_id, actor=payload.actor)
    return {"ok": ok, "message": message, "bill": bill}


@router.post("/bills/{bill_id}/remove-line")
def remove_line(bill_id: int, payload: RemoveLinePayload) -> dict[str, Any]:
    ok, message, bill = recon_service.remove_line(
        bill_id, payload.line_id, actor=payload.actor, reason=payload.reason
    )
    return {"ok": ok, "message": message, "bill": bill}


@router.post("/bills/{bill_id}/return")
def return_bill(bill_id: int, payload: ReturnPayload) -> dict[str, Any]:
    ok, message, bill = recon_service.return_bill(
        bill_id, actor=payload.actor, reason=payload.reason
    )
    return {"ok": ok, "message": message, "bill": bill}


@router.post("/bills/{bill_id}/sign")
def sign_bill(bill_id: int, payload: SignPayload) -> dict[str, Any]:
    """签发并入账；并发签发同一账单只会产生一条入账记录。"""
    ok, message, result = recon_service.sign_bill(bill_id, actor=payload.actor)
    return {"ok": ok, "message": message, **(result or {})}


# ---------------------------------------------------------------- 更正与越权
@router.post("/corrections")
def apply_correction(payload: CorrectionPayload) -> dict[str, Any]:
    try:
        return recon_service.apply_correction(
            contract_id=payload.contract_id,
            effective_date=payload.effective_date,
            reason=payload.reason,
            revoked_order_keys=payload.revoked_order_keys,
            replaced_end_date=payload.replaced_end_date,
            correction_id=payload.correction_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/unauthorized")
def mark_unauthorized(payload: UnauthorizedPayload) -> dict[str, Any]:
    try:
        return recon_service.mark_unauthorized(payload.line_id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------- 账本与月报
@router.get("/ledger")
def list_ledger(period: str = Query(default="")) -> dict[str, Any]:
    return {"items": recon_service.list_ledger(period=period)}


@router.get("/ledger/{entry_id}/recompute")
def recompute_entry(entry_id: int) -> dict[str, Any]:
    result = recon_service.recompute_entry(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"入账记录 {entry_id} 不存在")
    return result


@router.get("/reports")
def list_reports() -> dict[str, Any]:
    return {"items": recon_service.list_reports()}


@router.post("/reports")
def generate_report(payload: ReportPayload) -> dict[str, Any]:
    """从归属对账批次生成本期应付月报。"""
    try:
        return recon_service.generate_report(period=payload.period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports/{report_id}")
def get_report(report_id: int) -> dict[str, Any]:
    report = recon_service.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"应付月报 {report_id} 不存在")
    return report
