# Completion Review Report: Agent 基础能力与交付主路径

- Review date: 2026-07-23
- Review scope: Agent 基础使用链路，包括安装与启动、登录与权限、文件上传与知识库、Harness/专家委派、MCP 降级、运维证据源、checkpoint 前后端契约、测试门禁、部署与当前交付状态
- Review assumption: 本报告按 README 面向新使用者描述的默认能力审查，而不是只按已有 L1 试点豁免判断；不审查自动处置能力，也不执行任何 live 数据写入
- Source material: `README.md`、`AGENTS.md`、`Makefile`、`.env.example`、`pyproject.toml`、`app/`、`frontend/`、`mcp_servers/`、`deploy/compose/`、当前计划/交接、测试与当前工作树
- Verification commands: 前端 test/build、后端定向 pytest、后端全套 `-x`、60 秒 auth 测试超时保护、critical Ruff、`compileall`、Harness 文件行数检查、`git diff --check`；按用户要求未继续定位挂起测试，也未执行 live LLM/Milvus/Redis/canary 或数据写入

## Overall Conclusion

**结论：当前项目只适合配置明确、有人看护的本地或 L1 辅助试点，不具备可复现的一键交付条件，也不应作为生产 OnCall 主路径发布。** 项目已经具备 Harness、专家路由、SSE、RAG、记忆、checkpoint、前端过程面板和较多定向测试，并非概念 Demo；但 README 所描述的基础用户路径与代码默认行为没有闭环：标准安装缺默认 embedding 依赖，一键初始化无法自然通过 readiness/鉴权，默认上传不创建向量索引，实验 Compose 也不是完整运行栈。

更关键的是，核心运维证据并不等价于目标业务系统证据：变更源永久返回不可用，本地日志源只读本项目日志，默认指标源把任意 `service_name` 映射为 Agent 宿主机快照。与此同时，MCP 总开关在委派专家路径没有完全生效，默认凭据仍能登录且角色门禁默认旁路，后端完整测试门禁当前既会挂起也有真实失败。因此，现有 “L3 Conditional” 状态必须保持；在以下 P1 问题关闭前，不应宣称开箱可用、完整部署或生产就绪。

## Findings

### P1 - 默认上传不会进入知识库，但 README 宣称会创建索引

- Evidence: `app/api/file.py:48` 将 `auto_index` 默认设为 `False`；`frontend/src/api/fileApi.ts:23` 只提交 `file`；`Makefile:475` 的批量上传同样没有提交 `auto_index=true`；`app/services/file_storage_service.py:281` 仅在显式为 true 时同步索引。与此相反，`README.md:336` 将 `POST /api/files` 描述为“上传文件并创建向量索引”。仓库中有手动 `/reindex` 接口，但没有后台消费者自动处理默认 `pending` 文件。
- Impact: 前端或 `make upload` 显示上传成功后，文件仍可能永久停留在 `pending`，知识检索无法命中；这是最基础的 RAG 使用路径断裂。
- Recommendation: 先明确产品契约。若“上传”即“入库”，由前端和 Make 显式提交 `auto_index=true`，展示 `pending/indexed/failed` 状态并提供重试；若上传仅用于附件，应修改 README/界面文案，并提供独立、清晰的“加入知识库”动作。改变默认值前应单独评审同步索引的时延与失败语义。
- Verification: 从前端和批量命令各上传一个文档，轮询至 `indexed`，再用租户内 RAG 查询命中；索引失败必须可见且可重试。

### P1 - 标准安装和后端镜像无法使用默认 embedding

- Evidence: `app/config.py:44` 默认 `EMBEDDING_PROVIDER=local_bge_m3`；`app/services/vector_embedding_service.py:164` 首次加载时才导入 `FlagEmbedding`，缺少依赖会抛运行时错误。该依赖只在 `pyproject.toml:31` 的 `embedding` extra 中，而 README 快速开始只安装 `.[dev]`（`README.md:116`），`deploy/compose/Dockerfile.backend:11` 只安装基础包。`app/api/health.py:46` 仅按 provider 字符串把本地 embedding 标成可用，没有检查包、模型文件或实际加载能力。
- Impact: 新用户和 Compose 后端都可能成功启动、甚至通过部分健康检查，却在第一次上传索引或向量查询时失败；默认能力不是由默认安装提供的。
- Recommendation: 选择并固化一条默认路径：生产/快速开始安装 `.[embedding]` 并提供模型缓存与预取，或将默认 provider 改为标准安装确实具备的实现。readiness 至少检查依赖和模型制品是否可加载，并增加无外网冷启动说明。
- Verification: 在全新虚拟环境和新构建镜像中按 README 操作，完成一次 embedding、索引和检索；测试不得依赖开发机已有 Hugging Face 缓存。

### P1 - `make init` 的“一键初始化”契约不能自然完成

- Evidence: `Makefile:98` 的 `init` 顺序为启动 Milvus、启动 MCP/API、等待 `/health/readiness`、执行 `make upload`；`Makefile:112` 未获取或传递登录 token，而 `Makefile:459` 明确在 `AUTH_TOKEN` 为空时退出。`make up` 只启动 Milvus（`Makefile:134`），但代码默认 `REDIS_ENABLED=true`、`HARNESS_CHECKPOINT_ENABLED=true`（`app/config.py:289`、`app/config.py:303`），readiness 会把 Redis degraded 作为失败；默认 `admin/admin` 也会触发 readiness 安全失败。
- Impact: “一键初始化”通常会在等待阶段超时，或在最后上传阶段必然失败；用户不能依据 help/README 得到一个确定的可用环境。
- Recommendation: 将初始化改为可验证的 bootstrap：先做配置/依赖检查，启动所有必需依赖，创建或要求安全账号，登录取得短期 token，再执行明确的索引动作。若 Redis/checkpoint 对基础模式可选，则模板、代码默认值和 readiness 必须统一；不要在 init 中隐式依赖人工先设置 `AUTH_TOKEN`。
- Verification: 在干净工作目录仅按文档提供必要 secret 后运行 `make init`，命令 exit 0，readiness 为 200，文档已索引且一次受认证问答成功。

### P1 - “full” Compose 仍不是完整可运行栈

- Evidence: `deploy/compose/docker-compose.pilot.yml:1` 自身标注为 draft；服务只有 Redis、可选 Prometheus 和 backend，没有 Milvus、两个 MCP 服务或 frontend。backend 环境只覆盖 Redis，仍继承 `app/config.py:247`、`app/config.py:273`、`app/config.py:275` 中面向宿主机的 `localhost` Milvus/MCP 地址；其 healthcheck 又调用 readiness（Compose `:62`），因此完整 profile 无法仅靠该文件自然健康。
- Impact: 新机器无法用一个声明式入口复现产品；“full” profile 容易给出错误预期，并且容器网络中的 localhost 指向 backend 自身。
- Recommendation: 要么补齐 frontend、Milvus、MCP、网络 DNS、模型/数据卷和安全配置，形成真正的 full profile；要么把当前文件和 profile 明确命名为 backend skeleton，并在唯一主文档中组合已有 Compose/宿主机步骤。
- Verification: 在无本机服务兜底的环境执行一次完整拉起，所有 healthcheck 通过，登录、上传索引、RAG 问答、指标/日志工具和前端访问都可用。

### P1 - `HARNESS_MCP_ENABLED=false` 未覆盖委派专家路径

- Evidence: Harness 工具目录在 `app/agent/harness/registry.py:59` 会在开关关闭时移除 `mcp_server`；但专家 `get_tools()` 仍直接把 `monitor`/`cls` 传给 `app/agent/experts/base.py:157` 的 `collect_tools`，该函数没有读取总开关，并会在 `:178` 获取 MCP 客户端、在 `:185` 拉取远端工具。委派和 fallback 会调用这些专家，因此并非只有 registry 一条收集路径。
- Impact: 运维人员关闭 MCP 后，部分委派请求仍可能连接 MCP，造成不可预测的时延、网络访问和降级行为；环境变量不满足其宣称的降级边界。
- Recommendation: 在唯一 MCP 工具收集入口实施总开关，专家和 Harness 都复用该入口；为直接路由、单专家委派、并行委派和 fallback 各增加“关闭时零 MCP 调用”的测试。
- Verification: 注入会在调用时失败的 MCP client，设置 `HARNESS_MCP_ENABLED=false` 后遍历所有专家/委派路径，断言 client 调用次数为 0 且本地工具仍工作。

### P1 - 非 debug 默认值仍允许弱凭据登录，角色门禁也默认旁路

- Evidence: `app/config.py:326` 和 `app/config.py:330` 默认使用固定签名 secret 与 `admin:admin`；`debug` 默认 false。`app/api/health.py:218` 会把它们列入 readiness issue，但登录路由 `app/api/auth.py:21` 不受 readiness 状态阻断，默认账号仍可签发 token。`app/config.py:338` 默认关闭角色 enforcement，`app/services/authorization_service.py:29` 因而直接放行所有 `require_role` 调用；此外 `app/api/memory.py:174` 的 distill reject 写操作在 enforcement 打开时也没有调用 `require_role`。
- Impact: 直接按默认方式启动后，弱凭据可访问业务 API；普通已登录用户可以执行经验确认、索引重建、服务知识写入等 curator 操作。readiness 报警不能替代访问控制。
- Recommendation: 共享/非 debug 环境对默认 secret、默认账号和空角色映射 fail closed；完成角色映射后默认启用 enforcement，并补齐所有写路由的统一授权依赖。保留显式 local-demo 开关，而不是靠 readiness 提醒。
- Verification: 默认配置下业务登录被拒绝或进程拒绝就绪且不接业务流量；operator 对所有 curator/admin 写路由返回 403，curator/admin 授权矩阵全绿。

### P1 - 核心运维证据源与产品示例中的目标服务并不对应

- Evidence: `app/tools/change_tool.py:18` 将变更源固定为不可用；`mcp_servers/cls_server.py:3` 明确只读取本项目 `logs/`；默认 `MONITOR_TARGET_MODE=self`（`app/config.py:285`），本地 provider 在 `mcp_servers/monitor_server.py:291` 和 `:303` 明确把 `service_name` 仅作为兼容参数，将本机 CPU/内存快照扩展成时间序列。README 示例却使用 `checkout-api` 等目标业务服务进行诊断。
- Impact: Agent 可能对任意业务服务名返回 Agent 宿主机指标或本项目日志，形成“有数据但对象错误”的高风险证据；变更关联分析则没有真实数据可用。
- Recommendation: UI、SSE 和最终答案必须明确标记 source/capability/target，禁止把 self/local 数据表述成目标服务证据。试点环境默认使用真实 Prometheus/日志 provider；未接入时 fail closed 或明确回答能力缺失。变更源继续保持 unavailable，直到完成真实集成和租户授权。
- Verification: 对不存在的服务查询不得返回伪装成该服务的宿主机序列；每条证据包含 provider、实际 target、时间窗和 freshness；真实测试服务可交叉核对指标、日志和变更记录。

### P1 - 后端完整测试门禁当前不可执行且存在真实失败

- Evidence: `tests/test_auth_security.py:24` 的首个同步 `TestClient` 请求在 60 秒保护下稳定超时（exit 124）；线程栈显示主线程等待 AnyIO portal、事件循环空转。排除该文件后，全套 `-x` 运行到 `tests/test_harness_observability.py:446` 失败：强委派用例未产生 `complete`，当前测试替身只提供一个 LLM 响应，路径没有被完全封闭；该次结果为 183 passed 后 1 failed。`.pytest_cache` 也记录该用例为 last failed。
- Impact: CI 不能可靠给出成功或失败，安全回归可能永久挂住；Harness 的 complete 事件契约也没有被完整测试保护。
- Recommendation: 先修复 FastAPI lifespan/TestClient fixture，给测试级和作业级超时；封闭所有外部网络并为委派测试提供完整确定响应。然后把单次完整后端套件作为唯一发布门禁，不用拆组通过替代全套通过。
- Verification: 同一干净环境连续运行完整 suite 至少两次，均无挂起、无外网、无失败，且强委派始终以 `complete` 或显式 `error` 终止。

### P2 - “激进恢复”前端控件与后端默认能力冲突

- Evidence: `frontend/src/components/ChatWorkspace.tsx:156` 始终显示“激进恢复”开关，文案称只影响“下一次 send”；`frontend/src/App.tsx:494` 每次发送都透传当前布尔状态，发送后没有重置。后端 `app/api/assistant.py:263` 在默认 `HARNESS_CHECKPOINT_REQUEST_OVERRIDE_ENABLED=false` 时直接返回 403。
- Impact: 默认部署中用户可以选择一个必然被拒绝的功能；选择后还会持续影响后续请求，与“一次性”文案不符。
- Recommendation: 从后端 capability/config 端点驱动显示与禁用状态；成功或失败发送后重置一次性选项。若普通产品流量不允许 override，则移除该控件，只保留受控运维入口。
- Verification: 默认配置不展示或禁用控件；启用能力后只影响一次请求，下一次恢复为 false，并覆盖 403/成功两种前端测试。

### P2 - 配置模板、代码默认值和版本号存在多处漂移

- Evidence: `.env.example:159`、`:166` 把 Redis/checkpoint 标注为 false，而代码默认均为 true；`.env.example:62` 的 Harness token budget 为 16000，`app/config.py:85` 为 80000；`.env.example:130` 的日志行数为 20000，`app/config.py:236` 为 8000。`pyproject.toml:3` 的包版本为 1.2.1，API 默认 `app/config.py:24` 仍为 1.0.0。
- Impact: 同一个“默认部署”会因使用者相信模板、代码或 API 版本而表现不同，排障和回滚无法准确复现。
- Recommendation: 选定一个机器可校验的配置源，自动生成/校验 `.env.example` 和文档默认值；版本由构建元数据单向注入。为关键开关、预算和版本增加 contract test。
- Verification: CI 比较 Settings 默认值与模板声明无差异；包版本、OpenAPI 版本、health 版本和镜像标签一致。

### P2 - 当前实现状态尚不能形成可复现交付物

- Evidence: `docs/pilot/handoff-2026-07-19-unified-context-repository.md:5` 明确本棒仍在 dirty worktree 且未提交；当前 `git status --short` 仍包含大量核心上下文/Harness 修改和未跟踪文件。交接 `:26-29` 记录 live schema 仍为 v2、Redis 实例审计和 canary 未执行，统一开关必须保持 false。
- Impact: 当前验证对应的是本机工作树而非可检出的提交；其他人无法精确复现代码、schema、数据和测试组合，也不能把本地 focused 通过等同于 rollout 完成。
- Recommendation: 先按所有者划分并提交/交接当前工作树，固定 commit 与依赖锁定证据；live schema v3、projection apply、Redis 聚合和新会话 canary 继续作为四道独立授权门，不得合并执行或提前改默认值。
- Verification: 从固定 commit 在新环境重建并通过完整门禁；四道 live 门各有独立备份、命令、对账、回滚和批准记录。

## Completion Matrix

| Item | Status | Notes |
| --- | --- | --- |
| 登录与 token | Partial | 基本流程存在；默认弱凭据仍可登录，readiness 只告警不阻断 |
| 角色与写操作授权 | Partial | 有统一 `require_role`；默认关闭，且 distill reject 漏门禁 |
| Assistant SSE / Harness | Partial | 主链路已实现；全套测试中的委派 complete 契约失败 |
| 文件上传与租户存储 | Complete | 鉴权、owner scope、去重、下载和删除路径存在 |
| 上传后知识库索引 | Missing | 默认前端/Make 上传不触发索引，无后台索引器 |
| 默认 embedding 可安装性 | Missing | 默认 provider 的依赖不在标准安装和镜像中 |
| RAG 检索代码 | Partial | 代码与 tenant-scoped collection 存在；默认安装/上传主路径未闭环 |
| MCP 总开关 | Partial | Harness registry 生效，委派专家路径未完全生效 |
| 指标证据 | Partial | Prometheus 模式可配置；默认是与目标服务无关的本机快照 |
| 日志证据 | Partial | 本地日志工具可用；默认只覆盖 Agent 项目日志 |
| 变更证据 | Missing | 能力明确固定为 unavailable |
| Checkpoint 后端 | Partial | 保守恢复和 Redis 代码存在；默认依赖与部署不一致 |
| Checkpoint 前端 | Partial | 控件存在；默认必然 403 且一次性状态不重置 |
| 前端质量门禁 | Complete | 13 files / 83 tests 通过，production build 通过 |
| 后端完整质量门禁 | Missing | auth 测试挂起；排除后仍有 Harness observability 失败 |
| 一键初始化 | Missing | readiness 依赖和 upload token 契约不闭环 |
| 完整 Compose 部署 | Missing | 当前文件明确是 draft，缺 Milvus/MCP/frontend 与容器地址 |
| 配置与版本契约 | Partial | 多项默认值和版本来源不一致 |
| 统一上下文代码 | Partial | 本地 focused 证据充分；live rollout 四道门未执行 |
| 生产主路径就绪 | Missing | 仅允许受控 L1 辅助试点，L3 继续 Conditional |

## Test And Verification Notes

| 检查 | 结果 |
| --- | --- |
| 前端测试 | **13 files / 83 tests passed** |
| 前端 build | 通过；仅有现存 ineffective dynamic import warning |
| 后端定向拆分回归 | 两组分别 **49 passed**、**23 passed** |
| `tests/test_auth_security.py` | 60 秒超时，exit **124**；按用户要求不再继续定位 |
| 后端全套（排除 auth，`-x`） | **183 passed / 1 failed**；失败为 delegation duration/complete 用例 |
| critical Ruff / `compileall` / `git diff --check` | 通过 |
| Harness 单文件上限 | 通过；最大 `app/agent/harness/stream_inner.py` 为 **962 行** |
| live LLM / Milvus / Redis / canary | 未执行；本轮为只读项目审查，不取得数据写入或外部 rollout 授权 |

补充安全说明：本轮曾用 Compose 配置渲染检查结构，命令会把本地 `.env` 中的凭据展开到终端输出。报告不记录任何值，也不应再次使用未脱敏输出作为证据；建议立即轮换本地 LLM 与 auth 凭据，后续使用 dummy env、脱敏管道或不会解析 secret 值的配置校验方式。

## Next Steps

1. **先恢复可信门禁**：修复 auth 测试挂起与 delegation complete 失败，禁止测试回退到真实网络，完整后端 suite 连续两次通过。
2. **收紧基础安全**：默认 secret/账号 fail closed，启用并补齐角色授权矩阵；同时轮换本轮可能出现在终端输出中的本地凭据。
3. **打通黄金路径**：统一 embedding 安装、Redis/readiness、登录 token、上传索引和状态展示，使干净环境的 `init -> login -> upload -> retrieve -> answer` 可重复成功。
4. **纠正证据语义**：默认不把 self 指标或 Agent 日志冒充目标服务证据；接入真实 Prometheus/日志源，继续显式标记变更源不可用。
5. **统一开关与前后端契约**：修复 MCP 总开关和 checkpoint capability/一次性状态，补覆盖委派与降级路径的测试。
6. **形成可复现部署**：决定是补齐真正 full Compose，还是收敛为唯一、明确的宿主机部署方式；同步生成配置模板和版本。
7. **最后执行受控 rollout**：固定提交后，按既有交接分别申请 schema v3、projection apply、Redis 审计和 canary，不把本地代码完成等同于生产完成。
