"""报告生成领域服务：应付月报只从归属对账批次计算，不直接读原始明细。

模板保持原有字段与流转；额外提供 ``generate_payable_report``，由归属对账批次
里「有入账资格」的关联行汇总真实应付，并附上入账核对与未签发记录。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.supplier_recon import recon_service


class ReportBuilderService(BaseModuleService):
    MODULE = "report_builder"
    ENTITY = "报告模板"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["模板编号", "报告名称", "引用指标", "排版格式", "生成周期", "推送渠道", "最近生成", "模板状态"]
    REQUIRED_FIELDS = ["模板编号", "报告名称", "引用指标"]
    STATUS_ORDER = ["草稿", "已发布", "格式异常", "已停用"]
    ACTION_RULES = {"发布模板": "已发布", "标记异常": "格式异常", "停用模板": "已停用"}
    NEGATIVE_ACTIONS = ["停用模板"]

    def generate_payable_report(self, *, period: str = "2026-11") -> dict[str, Any]:
        """从归属对账批次生成应付月报。"""
        return recon_service.generate_report(period=period)

    def list_payable_reports(self) -> list[dict[str, Any]]:
        return recon_service.list_reports()

    def get_payable_report(self, report_id: int) -> dict[str, Any] | None:
        return recon_service.get_report(report_id)
