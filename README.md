# 数据仓库ETL任务编排平台

面向数据仓库ETL任务开发、作业编排调度、任务依赖解析、源端数据校验、异常任务重跑、血缘分板与运行全景监控的ETL工程管理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| ETL作业 | `etljob` | 作业定义 | 作业编号、作业名称、源端数据表 |
| 任务编排 | `dag` | 编排图 | 图编号、图名称、拓扑结构 |
| 依赖解析 | `dependency` | 依赖关系 | 关系编号、上游作业、下游作业 |
| 源端校验 | `sourcecheck` | 校验配置 | 配置编号、源端数据表、行数下限 |
| 运行实例 | `runtime` | 运行记录 | 记录编号、作业名称、开始时间 |
| 异常重跑 | `rerun` | 重跑任务 | 任务编号、原始运行编号、重跑区间开始 |
| 基线管理 | `baseline` | 基线定义 | 基线编号、基线名称、承诺完成时间 |
| 分区检测 | `partitioncheck` | 检测规则 | 规则编号、数据表名、期望分区数 |
| 数据比对 | `comparator` | 比对任务 | 任务编号、源表名、目标表名 |
| 元数据存储 | `metastore` | 元数据表 | 表编号、表名、所属数仓层 |
| 资源配置 | `resource` | 资源池 | 池编号、池名称、最大并发数 |
| SLA监控 | `slavalidate` | SLA规则 | 规则编号、适用作业、完成截止时间 |
| SQL模板 | `sqltpl` | 模板定义 | 模板编号、模板名称、模板类型 |
| 数据备份 | `backuprestore` | 备份策略 | 策略编号、数据表名、备份周期 |
| 日志归档 | `logarchive` | 归档策略 | 策略编号、日志类型、保留天数 |
| 通知渠道 | `notifychan` | 渠道配置 | 渠道编号、渠道类型、接收地址 |
| 通知投递事件 | `notifyevent` | 投递事件/批次/回执/快照 | 事件编号、幂等键、消息编号、送达结果、静默时段结果 |
| 发布计划 | `publishplan` | 发布单 | 发布编号、发布内容、变更说明 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 通知投递事件链路

通知渠道只有接收地址和模板时，异常升级消息缺少幂等键、投递尝试与确认回执。
`notifyevent` 模块把 SLA 升级事件与渠道投递数据收敛成可核对的投递事件，落库四张表：
`notifyevent`（投递事件）、`notifybatch`（投递批次）、`notifyreceipt`（确认回执）、
`notifyeventsnapshot`（事件数据快照）。

```text
多选SLA升级记录 ──一次提交──▶ 投递批次 ──按渠道展开+幂等键去重──▶ 投递事件
                                                                  │
                  确认回执（重复/冲突标记）◀── 投递尝试（失败可从最后尝试继续）
                                                                  │
                                          生成不可变快照 ──▶ SLA监控只依据快照核对升级
```

关键口径：

- **稳定幂等键**：`idempotency_key(SLA规则ID, 渠道ID)`（见
  `app/services/notifyevent_keys.py`），同一升级同一渠道重复提交只复用事件、不新增投递。
- **原子落库**：事件状态变更与确认回执在同一把写锁内完成；投递失败保留尝试次数，
  重置后从最后尝试继续，最大尝试 3 次。
- **并发只允许一次成功**：并发发送同一消息只形成一次送达结果与一条送达回执；
  重复回调登记为「重复回执」，不新增投递。
- **回执冲突取舍**：迟到的失败回执与已成功送达冲突时登记为「冲突回执」，不覆盖送达结果。
  多渠道路由按「SLA升级 × 值班组」内最早送达确定唯一采纳记录，其余成功送达标记冗余，
  采纳结论与重复通知次数不随回执到达顺序变化，渠道配置保持旧有口径不变。
- **快照核对**：快照是事件的不可变深拷贝（带校验和），SLA 监控通过
  `POST /api/slavalidate/{id}/confirm-delivery` 只读取快照，凭消息编号/回执编号确认
  跨模块结果来自同一次送达，并可核对异常升级消息、送达结果与静默时段结果。

后端测试：`cd backend && .venv/bin/python -m unittest tests.test_notifyevent -v`

领域：数据工程
子方向：数仓与数据湖
