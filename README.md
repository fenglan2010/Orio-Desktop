# OIRO · OTA 智能收益优化官

**仓库**：本仓库 [fenglan2010/Orio-Desktop](https://github.com/fenglan2010/Orio-Desktop) 为 OIRO **桌面 / 本地应用** 官方源码树（Electron 壳内嵌本机 FastAPI + Vue 前端，与从浏览器访问同一套产品能力）。Windows 打包与 `pack:dir` 验收步骤见 [desktop/README.md](desktop/README.md)。

```powershell
git clone https://github.com/fenglan2010/Orio-Desktop.git Orio-Desktop
cd Orio-Desktop
```

可本地运行的中文收益运营工作台（Vue 3 + FastAPI + LangGraph）：看见问题 → 看懂原因 → 给出策略 → 人审（可选的低风险自动执行）→ 留下沙盒执行记录。

**主线分发方式（2026）**

| 方式 | 说明 |
|------|------|
| [开源从源码](docs/安装指南.md) | `make dev`，**无需**安装密钥；适合贡献者与酒店内网 |
| [官方桌面包](desktop/README.md) | Electron 壳 + 本机 SQLite；**需要**安装密钥（`OIRO_DISTRIBUTION=packaged`） |
| Docker / K8s（可选） | 见下方「部署」与 `deploy/`，历史/单机栈 |

中文文档：[安装指南](docs/安装指南.md) · [配置指南](docs/配置指南.md) · [操作运行指南](docs/操作运行指南.md) · 开源协议 [MIT](LICENSE)

本仓库按 `docs/` 中的产品定义实现。`docs/source/` 是早期设想归档，冲突时以文档正文为准。

## 启动（从源码，推荐入门）

在项目根目录执行：

```bash
make dev
# 或
bash scripts/dev.sh
```

- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000
- 健康检查：http://127.0.0.1:8000/api/health
- **默认演示账号：`admin` / `admin123`**（杭州西湖精品酒店）。成都春熙商务酒店演示账号：`admin_b` / `admin123`。

首次启动会创建 `.venv`、安装依赖、写入 `data/oiro.db` 并导入演示数据。前端通过 Vite 把 `/api` 代理到后端，CORS 已允许本地 5173 端口。

模型密钥可在 **系统设置 → 本机配置** 保存（仅存本机），或复制 `.env.example` 为 `.env` 填入（开发用）。没有密钥时，DeepSeek / 通义会自动降级到本地确定性模拟器，主流程仍可完整演示。

桌面一体启动（后端托管 `frontend/dist`）：

```bash
bash scripts/run-desktop-source.sh
```

Windows 安装包构建见 `desktop/scripts/build-windows.ps1`（须在 Windows 上执行；本仓库不附带已签名安装程序）。

## 重置演示数据

```bash
make reset
# 或
bash scripts/reset_db.sh
```

普通启动会按「今天」滚动刷新近 14 天 + 未来 14 天的模拟指标，但**不会打断已有人审、执行与审计记录**。`reset` 会清空数据库后重新导入，人审历史会被删除。

## 数据库

本地默认是 SQLite（`data/oiro.db`），无需额外服务。切换 PostgreSQL 时设置：

```bash
# 腾讯云 TencentDB / 阿里云 RDS：优先私网地址，并要求 TLS
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@PRIVATE_HOST:5432/oiro?sslmode=require
```

也接受 `postgresql://` 或 `postgres://`。不要把带密码的 URL 写进仓库或打到日志。上云前必须更换默认的 `JWT_SECRET`。LangGraph 检查点在 SQLite 下写入 `*.langgraph.db`，在 PostgreSQL 下写入同一库的 `langgraph` schema，进程重启后仍可恢复待人审运行。完整说明见 [docs/09-第三阶段.md](docs/09-第三阶段.md)。任务队列默认用本地调度进程，无需 Redis；配置 broker 后可改用 Celery。生产形态的部署用 Docker Compose 或 Kubernetes，说明见下方「部署」和 [docs/09-第三阶段.md](docs/09-第三阶段.md)。

可选的本地测试 Postgres（仅 127.0.0.1，带密码）：

```bash
bash scripts/run_test_postgres.sh start
source /tmp/oiro-pgtest/env.sh
make test
```

## 部署（可选 / 历史）

本地桌面与从源码运行继续用上面的 `make dev` 或 `scripts/run-desktop-source.sh`（SQLite）。**不要把密钥或用户契约提交到 Git**；参见 `.gitignore`。

要在本机或单机虚机拉起生产形态的栈（前端、API、Postgres、Redis、Celery worker / beat）：

```bash
cp deploy/compose.env.example deploy/compose.env
# 填写 JWT_SECRET 与 POSTGRES_PASSWORD（不要使用 oiro-demo-secret-change-me）
docker compose --env-file deploy/compose.env up -d --build
```

- 网页入口：http://127.0.0.1:8080
- API（仅本机）：http://127.0.0.1:18000/api/health
- Postgres 与 Redis 只在内部网络，不映射到主机端口。

云上生产优先使用同一 VPC 内的腾讯云 TencentDB / 阿里云 RDS PostgreSQL 和托管 Redis，并在 `DATABASE_URL` 上加 `sslmode=require`（或 verify-ca / verify-full）。Kubernetes 清单在 `deploy/k8s/`（含 TKE / ACK overlay 占位符）。设置 `OIRO_ENV=production` 后，进程会拒绝演示 JWT 密钥，也不会静默使用 SQLite。完整步骤、安全清单和「本增量未包含的内容」见 [docs/09-第三阶段.md](docs/09-第三阶段.md) 与 [deploy/README.md](deploy/README.md)。

## 运行测试

```bash
make test
```

覆盖：关键动作在境外模型开启时仍不出境；人审通过 / 拒绝 / 回滚写入审计；无密钥时看板、生成策略、人审可跑通。

前端验收构建：

```bash
make build
```

## 演示故事（模拟数据，不是真实 OTA）

现有两家互相隔离的演示酒店，平台均为**携程（模拟）**。时间窗口以当天为基准，近 14 天历史 + 未来 14 天可定价。

- **杭州西湖精品酒店（演示）**：豪华大床房、商务双床房、家庭房。近一周搜索排名下滑，西湖和禧、湖滨商务等竞对降价 12%–18%，我方挂牌价基本未动，形成**价格倒挂**，并有一条提及噪音与卫生的 2 分差评。账号：`admin` / `admin123`、`manager` / `manager123`、`approver` / `approver123`、`analyst` / `analyst123`。
- **成都春熙商务酒店（演示）**：高级大床房、商务双床房。工作日出租率弱于周末，并有一条提到地下停车场车位紧张的 3 分评价。这是另一家店的故事，不是杭州数据的复制。账号：`admin_b` / `admin123`（只能看见成都店）。

在执行中心可生成调价与活动参与建议，看到价格区间、理由、置信度、风险等级和决策路径。默认须人审；酒店也可在设置或 L4 页开启低风险自动执行。通过后写入官方沙盒回执（不是真实携程调价），并可按 `checkpoint_id` 回滚。两家酒店的策略、预警、账号和执行策略互不可见。

## 实现要点

- 控制流节点：`data_node → signal_node → analysis_node → strategy_node → human_review → execution_node → memory_node`。`memory_node` 沉淀本店已执行或已否决的决策，并写入匿名行业模式供跨店引用；本店记忆不跨店，也不编造调价收益。
- 优先使用 **LangGraph**；若环境无法安装，则使用节点名称与语义等价的状态机，并在系统设置页标明。
- 模型网关：统一协议；按任务路由，不按厂商写业务。默认国内链路为 DeepSeek（主）→ Qwen（备）→ 自建开源模型（预留，默认关闭）→ Mock。境外适配器 OpenAI / Gemini / Grok 默认关闭。`pricing_strategy` 与 `review_reply` 即使开关打开也绝不出境；自建开源端点视为国内，关键任务可以使用，但仍不得出境。任务偏好、限流、排队与熔断只在服务器环境或内部运维配置，单 API 进程内生效（多副本需后续接入共享限流）。酒店设置不配置、也不展示模型路由、适配器、境内外开关、自建开源地址或密钥。无密钥或未填写自建地址时自动跳过并降级，主流程仍可演示。
- 价格区间由可解释启发式计算，大模型只写理由，不得直接给出不可复核的执行价。
- 风险等级 `low / medium / high` 用于展示、审计和放行。**默认自动执行关闭**，行为与第一阶段相同。酒店开启后，仅低风险、非节假日、调价幅度低于阈值（默认 15%）的方案可自动执行；中高风险、活动报名和评价回复仍必须人审。自动执行审计操作人为 `system:auto_execute`，并保留检查点与回滚。
- 执行前写入 Checkpoint，回滚绑定 `checkpoint_id`。界面可追溯一条策略经过的节点。

## 页面

杭州店演示账号：`admin` / `admin123`（酒店管理员）、`manager` / `manager123`（收益经理）、`approver` / `approver123`（调价审批人）、`analyst` / `analyst123`（运营观察）。成都店：`admin_b` / `admin123`（仅该店管理员）。内部运营后台演示账号（入口 `#/ops/login`，均非酒店账号）：管理员 `ops` / `ops123`，合同 `ops_contract` / `contract123`，系统运维 `ops_sysops` / `sysops123`，运营 `ops_operations` / `operations123`，客服 `ops_support` / `support123`。产品设计不是可登录岗位。官方契约由合同岗安装，线上写入须管理员与合同双人确认。新酒店须完成商务合同与付款确认后才开通产品权限；电子签与支付供应商尚未接通。详见 [docs/11-内部岗位与开通流程.md](docs/11-内部岗位与开通流程.md)。权限由固定角色矩阵控制：管理员可管理本店账号、执行策略，以及授权本店 OTA 账号密码；收益经理可审批并执行但不能改策略、渠道或账号；审批人可审批但不能执行；运营观察仅查看 L1–L2。账号只能看见自己所属酒店的数据。官方契约、接口地址和官方接口密钥由运维端配置，不会出现在酒店设置里。

登录后为 L1–L5 分层导航（视觉与信息架构对齐 Figma 雏形，产品名仍为 OIRO / 智能收益优化官）：

- `#/dashboard`（`#/l1`）L1 实时监控大屏：真实 KPI、房型、趋势与预警诊断（通知开关会过滤对应类型的未关闭预警）
- `#/analysis`（`#/l2`）L2 多维分析与策略建议（一键生成）
- `#/execution`（`#/l3`，页签 `tab=`）L3 执行中心：待人审队列、评价回复、写入监控（沙盒或真实官方回执、对账与重试）
- `#/autonomy`（`#/l4`）L4 自治管理（可开关与设置页相同的低风险自动执行策略；默认关闭；渠道连接状态；日志接审计）
- `#/evolution`（`#/l5`）L5 本店经验沉淀与匿名行业模式（跨店学习已开始，但是匿名模式，不展示其他酒店名称，不会训练模型，不会自动改价；已执行调价可对照决策时基线观察变化，不是因果收入）
- `#/analysis`（`#/l2`）L2 多维分析：需求、库存压力、预订节奏、历史对比、渠道转化、竞对与房型；支持按日期因果诊断。渠道净贡献暂用房型营收替代，取消/活动/客群/弹性/佣金/直销因缺少独立数据保持空态
- `#/execution`（`#/l3`）L3 执行中心：调价建议、活动参与建议、评价回复、决策路径追溯；库存滑条与自动保护为本阶段预览
- `#/settings?section=` 系统设置（酒店管理员可管理账号、执行策略，并授权本店 OTA 账号密码；收益经理可改价格约束；全员可查看真实角色权限矩阵。官方契约和接口由运维端配置。模型路由、适配器状态、境内外开关和原始 API 密钥不在酒店用户侧展示；PMS/CRS 为官方 API 预留接入，由运维端配置契约，不包含轻量 PMS）
- `#/ops` 内部运营后台（独立登录，不在酒店侧栏；按岗位分区：商务开通、酒店状态、官方 OTA/PMS 契约、员工与岗位、审计、系统状态）
- `#/audit` 日志查看（侧栏「日志查看」进入）
- 决策对话在顶栏打开抽屉；旧地址 `#/alerts`、`#/strategies`、`#/reviews`、`#/chat` 会重定向到新位置

