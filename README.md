# 洞察工坊 · 多维交互分析与实验度量平台

面向数据分析师的多维下钻分析、指标口径管理、A/B实验评估、群体圈选分群、归因分析与数据看板构建的交互分析平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/SupplierReconView.vue 供应商归属对账工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装（recon.ts 为归属对账接口）
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/recon.py     归属对账、账单流转与应付月报接口
│   ├── app/domain/supplier_recon.py  合同归属/生效日核验（纯函数）
│   ├── app/services/supplier_recon.py 批次、账单、账本与报告
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标口径 | 指标编号、指标名称、计算口径 |
| 维度定义 | `dimension_def` | 分析维度 | 维度编号、维度名称、字段来源 |
| 多维查询 | `olap_query` | 查询方案 | 方案编号、查询名称、选择指标 |
| 群体圈选 | `segment_builder` | 人群包 | 人群编号、人群名称、圈选条件 |
| A/B实验 | `ab_experiment` | 实验方案 | 方案编号、实验名称、实验分组 |
| 实验分析 | `experiment_result` | 实验报告 | 报告编号、关联实验、指标名称 |
| 归因分析 | `attribution_model` | 归因模型 | 模型编号、模型名称、归因算法 |
| 看板定义 | `dashboard_def` | 数据看板 | 看板编号、看板名称、布局配置 |
| 下钻路径 | `drill_path` | 下钻方案 | 方案编号、父级维度、子级维度 |
| 同期群分析 | `cohort_analysis` | 同期群 | 群组编号、群组名称、划分周期 |
| 数据导出 | `data_export` | 导出任务 | 任务编号、数据来源、导出格式 |
| 查询缓存 | `query_cache` | 缓存条目 | 条目编号、查询哈希、结果行数 |
| 定时分析 | `scheduled_analysis` | 调度任务 | 任务编号、分析方案、Cron表达式 |
| 指标对比 | `metric_compare` | 对比方案 | 方案编号、主指标、对比指标 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则编号、目标指标、检测算法 |
| 分析快照 | `data_snapshot` | 数据快照 | 快照编号、分析方案、数据行数 |
| 分群比较 | `user_segment` | 比较方案 | 方案编号、群体A、群体B |
| 事件追踪 | `event_track` | 事件定义 | 事件编号、事件名称、事件属性 |
| 排行榜 | `ranking_board` | 排行方案 | 方案编号、排行指标、排行维度 |
| 漏斗分析 | `funnel_def` | 漏斗方案 | 方案编号、步骤序列、转化窗口 |
| 数据集关联 | `dataset_join` | 关联方案 | 方案编号、主数据集、关联数据集 |
| 报告生成 | `report_builder` | 报告模板 | 模板编号、报告名称、引用指标 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 供应商归属对账

供应商明细原先只按业务键（订单号）关联，同一订单号在甲、乙公司各有一条时无法判断
哪条属于本次报表。新增的归属对账层在业务键之上再核验三件事：合同归属、合同有效期
（含跨月结束日）、以及报表部门。

| 入口 | 地址 | 用途 |
| --- | --- | --- |
| 前端工作台 | `/supplier_recon` | 关联入口、批次、账单流转与月报 |
| 关联入口 | `GET /api/recon/association?period=2026-11&company=甲公司&order_key=SO-20261101` | 两公司、日期边界、拒绝原因 |
| 归属批次 | `POST /api/recon/batches` | 按合同与生效日生成批次和待签账单 |
| 账单 | `POST /api/recon/bills/{id}/claim|remove-line|return|sign` | 领取/剔除/退回复核/签发 |
| 合同更正 | `POST /api/recon/corrections` | 生效日早于交易时撤回原资格 |
| 应付月报 | `POST /api/recon/reports` | 只从批次取有证明的入账行 |

核验结论与拒绝码：`ELIGIBLE` 合格、`OUT_OF_PERIOD` 不在对账期、`NO_CONTRACT`
无合同归属、`EXPIRED_ORDER` 过期订单、`WRONG_DEPARTMENT` 错部门、
`CONTRACT_REVOKED` 合同更正撤回、`UNAUTHORIZED` 越权关联、`REMOVED` 已剔除。
「过期订单」和「错部门」是两个独立拒绝码，便于区分是日期越界还是归属公司/部门不对。

账单流转：`待领取 → 已领取 → 已签发`，已领取可 `退回复核` 或剔除不合格行；
退回复核的账单在重新领取前本期不入账。签发时按当前合同/更正/越权状态重新核验，
存在未剔除的不合格行一律拒绝签发；进程锁 + 账本按账单唯一，同一账单并发签发只入账
一次；入账记录冻结签发时的行级口径与哈希，之后合同再更正也能按历史口径复算。

领域：数据工程
子方向：数据分析与数据应用
