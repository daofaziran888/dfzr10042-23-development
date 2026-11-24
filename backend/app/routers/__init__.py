"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import recon, resource

# recon 的固定路径（/api/recon/...）必须早于 resource 的 /api/{module} 注册，
# 否则 /api/recon 会被当作业务模块名。
ROUTERS = [recon, resource]
