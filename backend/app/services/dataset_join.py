"""数据集关联领域服务：保留通用关联方案管理，并作为归属对账的关联入口。

关联入口把「按业务键关联」的原始明细完整暴露出来：每个订单号在哪些公司各有一条、
各自命中哪份合同、合同有效期是否覆盖交易日、以及最终拒绝原因。归属对账批次
只采用这里核验出「有入账资格」的行。
"""
from __future__ import annotations

from typing import Any

from app.domain.supplier_recon import REJECTION_LABELS
from app.services.base import BaseModuleService
from app.services.supplier_recon import recon_service


class DatasetJoinService(BaseModuleService):
    MODULE = "dataset_join"
    ENTITY = "关联方案"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["方案编号", "主数据集", "关联数据集", "关联键", "连接类型", "字段映射", "结果行数", "方案状态"]
    REQUIRED_FIELDS = ["方案编号", "主数据集", "关联数据集"]
    STATUS_ORDER = ["草稿", "已执行", "字段冲突", "已清理"]
    ACTION_RULES = {"执行关联": "已执行", "标记冲突": "字段冲突", "清理方案": "已清理"}
    NEGATIVE_ACTIONS = ["标记冲突", "清理方案"]

    def association_view(
        self,
        *,
        period: str = "2026-11",
        company: str = "",
        order_key: str = "",
    ) -> dict[str, Any]:
        """关联入口：按订单号看两公司的关联行、日期边界和拒绝原因。"""
        return recon_service.association_view(
            period=period, company=company, order_key=order_key
        )

    def rejection_catalog(self) -> dict[str, str]:
        return dict(REJECTION_LABELS)
