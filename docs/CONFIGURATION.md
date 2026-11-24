# 配置、接口与实现细节

把**任意数据库**接入同一个平台，用中文直接问数据，返回 SQL、结果表、图表和一句结论。

## 快速开始

项目评测集已补充到 [backend/eval/README.md](../backend/eval/README.md)：104 条自动用例（带标准 SQL、6轮追问、只读护栏）和12项人工验收；完整题目见 [CASES.md](../backend/eval/CASES.md)。在项目根目录运行 `python backend/scripts/eval_run.py --mode validate` 可先校验题集，`--mode llm` 执行真实模型评测。

```powershell
cd nl2sql-platform\backend
python -m pip install -r requirements.txt
python scripts/seed_demo.py          # 首次运行：生成演示电商库 + 注册数据源/指标
.\run.ps1                            # 启动 http://127.0.0.1:8765
```

浏览器打开 <http://127.0.0.1:8765>，左侧已有一个“演示电商库”，直接提问即可。

项目提供一套可生成的合成测试数据：`backend/data/testdata/`，15 个独立 SQLite 库、约 290 万行、246 MB，覆盖电商零售 / SaaS 订阅 / 物流履约 / 银行金融 / 医院医疗 / 在线教育 / 制造供应链 7 个域（每域 A/B 两个变体），另加 1 个 150 表宽库。生成后会注册到平台，启动后左侧可见；每库的多表联查示例问题见 `backend/data/testdata/TEST_QUESTIONS.md`。首次使用或需要重新生成时：

```powershell
cd backend
python scripts/generate_testdata.py --scale 0.8
```


前端源码在 `frontend/`（Vue 3 SFC + Vite + ECharts），构建产物在 `frontend/dist/`，后端只负责托管：

- 生产：FastAPI 首页读 `frontend/dist/index.html`，静态资源走 `/static/...`；
- 开发：先跑后端 8765，再 `cd frontend && npm run dev`，Vite 5173 会把 `/api` 代理到 8765；
- 改完前端必须 `npm run build`，或直接用 Vite dev 端口调试；只刷新 8765 不会自动更新。

不配置大模型也能跑：系统会自动降级为**规则引擎**，覆盖单表聚合、分组、时间范围、排名这类常见问法，用来验证整条链路。要获得真正的泛化能力（多表 JOIN、复杂条件、口径推理），配置一个 OpenAI 兼容模型：

```powershell
cd backend
$env:LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:LLM_API_KEY = "sk-xxx"
$env:LLM_MODEL   = "qwen-plus"
python -m uvicorn app.main:app --port 8765
```

DeepSeek、Moonshot、本地 vLLM / Ollama（`http://127.0.0.1:11434/v1`）都兼容，只需改 `LLM_BASE_URL` 与 `LLM_MODEL`。

### 模型配置的三个坑

1. **三项必须齐全**：`LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`，缺一项就退回规则引擎。只配 Key 不配端点和模型名等于没配。
2. **变量名要对**：平台读的是上面这三个；`MINIMAX_API_KEY` 只有在 `LLM_BASE_URL` 里带 `minimax` 时才会被当作 Key 使用，`OPENAI_API_KEY` / `OPENAI_BASE_URL` 也可作为别名。
3. **改完必须重启**：配置在进程启动时读取，热改 `.env` 或环境变量不会生效。

推荐把配置写进 `backend/.env`（参考仓库中的 `backend/.env.example`），启动脚本会自动读；也可以在 `backend` 目录下显式传参：

```powershell
.\run.ps1 -LlmBaseUrl http://127.0.0.1:11434/v1 -LlmApiKey ollama -LlmModel qwen2.5:7b
```

页面上那个模型角标**点一下就会真发一次请求**（`GET /api/llm/test`），失败时直接把 HTTP 状态码、端点、模型名和报错原文弹出来。首页角标显示“缺 LLM_BASE_URL/…”就是配置没到位；显示“模型：xxx”只代表三项非空，不代表 Key 真的能用——点一下才算验过。

另外，模型调用失败时平台会自动退回规则引擎，但会在回答卡片顶部显示黄色提示条，不会再悄悄降级。


## 表召回策略：≤1000 张表全量注入

先把两件事分开：**“查询能碰哪些表”和“模型能看到哪些表”不是一回事**。护栏的白名单是整库所有表，SQL 里 JOIN 任何业务表都放行；注入数量只决定模型能看到多少 DDL。

当前策略（`NL2SQL_SCHEMA_MODE=auto`）：

| 库规模 | 行为 |
|---|---|
| ≤ 1000 张表 | **全库注入，不裁剪表、不裁剪列** |
| > 1000 张表 | 按表名/注释/指标主表做相关度召回，默认前 8 张（`NL2SQL_TOP_K`） |
| 追问 | 在上轮用过的表基础上补召回，不让会话漂表 |

全量注入的代价要清楚：120 张表 / 720 列约 **18,236 字符**，1000 张表大约 **15 万字符**。轨迹里会显示实际 DDL 字符数，超过 4 万字符会提示“模型上下文可能不够”。这不会自动截断（按你的要求放开了），但如果模型上下文不够，服务端会收到截断后的提示词或直接报错。

可调参数：

```
NL2SQL_SCHEMA_MODE=auto|all|recall   # auto 自适应、all 强制全量、recall 永远只召回
NL2SQL_TOP_K=8                      # 超过 1000 张表时的召回张数
NL2SQL_FULL_SCHEMA_MAX=1000          # 多少张表以内直接全量
NL2SQL_MAX_SCHEMA_TABLES=1000        # 全量模式硬上限
NL2SQL_SCHEMA_WARN_CHARS=40000       # 超过多少字符在轨迹里提示上下文风险
```

1000 张表以上仍建议做“表级召回 + 列裁剪 + 二次召回”，全量注入只适合中小库；模型上下文不够时，宁可换大上下文模型，也不要让服务端偷偷截断（现在不会截断，只在轨迹里提示风险）。

## 展示行数 vs 全量数据

页面一次只展示 200 行（`NL2SQL_MAX_ROWS`），这是**展示上限**，不是查询上限。要看全表，两条路：

1. **导出完整结果（CSV）**：结果被截断时回答卡片会出现按钮，调 `GET /api/export/{log_id}?connection_id=xxx`，服务端按日志重放原 SQL、流式写 CSV（带 BOM，Excel 双击不乱码），不进浏览器内存。默认最多导 20 万行（`NL2SQL_EXPORT_MAX_ROWS`）。
2. **调大页面展示行数**：`NL2SQL_MAX_ROWS=5000` 后重启；浏览器渲染几千行会明显变慢，超过几千行建议用导出。

导出有个细节：平台自动补的 `LIMIT 200` 不会带到导出里（日志里记了 `limit_applied` 标记），但用户自己写的 `LIMIT 5`（比如“销售额前 5 名”）会原样保留，不会被导出成全表。

## 多轮追问与会话记忆

会话记忆存在服务端 `data/platform.db` 的三张表里（`conversations` + `conversation_turns` + `conversation_memory`），**轮次不设上限，完整落库**，不在前端截断：

- **每轮回答原样存一份完整 payload**：问题、SQL、摘要、列、结果行、图表建议、执行轨迹、告警、口径说明、`log_id`、上下文轮数都在 `conversation_turns.payload` 里，历史回放能重画表格、图表、轨迹，并可直接导出当时结果；
- 升级前的旧轮次只有问题/摘要/SQL，结果明细当时没有落库，无法追溯补回，页面会明确标注；
- 前端只保存 `conversation_id`（按 `connection_id` 存 localStorage），刷新页面后仍能接上原来的会话；
- 页面「会话历史」按数据源列出**全部会话**（标题、时间、轮次数），点任意一个会在右侧对话区**完整回放**该会话的每一轮问题、摘要和 SQL，并把它设为当前会话；每条会话都有「删除」入口，删当前会话会同步清空本地指针；「新会话」只清当前指针，不会隐藏旧记录，窄屏下侧栏也不再被隐藏；
- 回答卡片支持一键**复制答案**和**复制 SQL**，实时回答与历史回放都能复制；
- 每次提问把 `conversation_id` 传回后端，后端从库里取**全部历史轮次**，据此判断是不是追问：
  - 短句不再一律当追问：只有和上一轮共享实体词、共享日期上下文，或含“那 / 它 / 去掉 / 换成 / 还是 / 再 / 回到第 N 轮”这类指代与修改词，才继承上文；
  - 追问时召回用「上一轮问题 + 当前问题」一起检索，并强制保留上一轮真正用到的表；
  - 提示词把上一条 SQL 交给模型，要求“只改用户明确提到的部分”，表、关联、过滤条件保持不变；
  - “去掉 limit / 不要 limit”不交给模型，直接按上一条 SQL 去掉 LIMIT；执行层仍按 200 行截断并标“已截断”；
  - 追问且强调“去掉 limit”时不触发“空结果自动放宽”，避免把日期过滤条件偷偷删掉。
- **结构化记忆压缩，不丢事实**：每轮执行后从 SQL AST 抽取表、指标、日期条件、过滤条件、分组、排序、LIMIT，存进 `conversation_memory` 状态卡；提示词由三部分组成：
  - 状态卡：当前主题 + 显式条件 + 最近 12 个主题索引；
  - 近期原文：最近 4 轮完整问题 / SQL / 结果摘要；
  - 相关早期轮次：从更早的轮次里按关键词、表、指标相关度取 Top-6，不按时间硬塞；说“回到第 3 轮”时直接按轮次号定位。
  摘要由已执行 SQL 的结构化信息生成，不调用模型转述，避免“摘要的摘要”越滚越偏；完整原文始终在 `conversation_turns.payload`，会话历史与导出不受影响。老会话第一次追问时没有状态卡，会自动从完整历史重建。
- **记忆按数据源隔离**：会话绑定 `conn_id`，跨库传 `conversation_id` 会直接 400；历史里某一轮用了当前库 catalog 中不存在的表，只丢弃那一轮，不再让一次脏表名把整段长期记忆清空。
- 每轮回答带 `context_used` / `context_turns`，页面显示“继承上文 N 轮”；执行轨迹新增“记忆装配”，显示状态卡字符数、近期轮数、相关早期轮数。

记忆预算可调：

```
NL2SQL_CONTEXT_RECENT=4          # 近期给完整原文的轮数
NL2SQL_MEMORY_STATE_CHARS=1200   # 状态卡字符上限
NL2SQL_MEMORY_OLD_LIMIT=6        # 相关早期轮次最多取几条
NL2SQL_MEMORY_TOPICS=12          # 状态卡里保留多少个近期主题索引
```

状态卡只记录 SQL 里的显式条件，不把结果数字写进长期记忆；这样不会出现“摘要里编了一个数”的二次幻觉。

接口：

```
POST   /api/ask                                 {"connection_id","question","conversation_id"?}
GET    /api/conversations?connection_id=xxx     列该库的会话（含轮次数）
GET    /api/conversations/{id}/turns            全部轮次（limit=0 表示不限）
DELETE /api/conversations/{id}                  删会话
```

`history` 字段仍保留兼容旧调用；有 `conversation_id` 时以服务端落库历史为准。

```json
{"connection_id": "xxx", "question": "去掉 limit 试试", "conversation_id": "83df76e73b16"}
```

页面左侧「会话历史」可展开看该库全部轮次（问题、时间、行数、SQL），「新会话」只断开当前会话，旧记录仍留在库里。

另外，自省阶段会对 `status / type / level / channel` 这类枚举列采样前 200 行的真实取值（最多 10 个），写进提示词，例如 `status -- 取值: on_sale / off_sale`。这能避免模型把 `on_sale` 翻译成 `在售` 导致结果全为 0。

## 架构

```
前端（Vue 3 SFC · frontend/ 源码 → frontend/dist 构建产物）
        │  POST /api/ask
        ▼
backend/app/api/          路由层：system / connections / query / metrics
        ▼
backend/app/services/     业务层：数据源接入、问数编排、指标口径、日志
        ▼
backend/app/agent/        智能体层：意图 → 召回 → 生成 → 执行/自修 → 解释
        ▼
backend/app/db/           数据访问层：连接与方言、schema 自省、只读执行、元数据
        ▲
backend/app/schemas/      数据契约：catalog 辅助、API 入参
backend/app/core/         配置、依赖单例、异常
backend/app/llm/          模型客户端与提示词
```

分层原则：**路由只做参数校验与转发，业务不碰 SQL，节点不碰 HTTP，数据访问不碰业务规则**。新增一种数据库只需改 `db/engine.py` 的方言映射；新增一个问数节点只在 `agent/` 加一个文件，再在 `pipeline.py` 挂上。

关键设计是**方言无关的统一层**：不管底层是 MySQL、PostgreSQL、SQL Server 还是 Oracle，平台内部只认一份 `catalog`（表、列、类型、注释、主外键）和一份 `sqlglot` 方言名；生成 SQL 时按真实方言写函数，校验时按真实方言解析、按真实方言回写。

问数流水线的每个节点都是独立文件（均位于 `backend/app/`），只通过 `QueryState` 传递状态：

| 节点 | 文件 | 职责 |
|---|---|---|
| 意图识别 | `agent/intent.py` | 结构类问题直读目录，数据类问题进入链路 |
| 模式召回 | `agent/retrieve.py` | 中文 bigram + 指标别名召回表，渲染紧凑 DDL |
| SQL 生成 | `agent/generate.py` + `rule_engine.py` | 模型优先，不可用时规则兜底 |
| SQL 校验 | `agent/guard.py` | 单语句、只读、白名单、系统库、自动 LIMIT |
| 执行自修 | `agent/execute.py` | 执行、按报错重写（最多两次）、空结果放宽 |
| 结果解释 | `agent/explain.py` | 图表类型推断、一句话结论 |

## 接入一个新数据库

左侧“新增数据源”填连接串即可，平台会立刻连上去做自省（表、列、类型、注释、主外键），并落成本地 schema 目录。常用连接串：

| 数据库 | 连接串 | 需要安装的驱动 |
|---|---|---|
| SQLite | `sqlite:///xxx.db`（相对 `backend/data`）或绝对路径 | 内置 |
| MySQL / MariaDB | `mysql+pymysql://user:pass@host:3306/db` | `pip install pymysql` |
| PostgreSQL | `postgresql+psycopg://user:pass@host:5432/db` | `pip install psycopg[binary]` |
| SQL Server | `mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+17+for+SQL+Server` | `pip install pyodbc` + ODBC 驱动 |
| Oracle | `oracle+oracledb://user:pass@host:1521/?service_name=ORCL` | `pip install oracledb` |
| ClickHouse / DuckDB / Trino | 对应 SQLAlchemy 方言 URL | 对应方言包 |

**建议给平台单独开一个只读账号**（`GRANT SELECT`），这是最后一道防线；平台自身还有 SQL 解析层的只读护栏。

## 指标口径：决定准确率的关键

在左侧“指标口径”里维护业务定义，例如：

- GMV：别名 `销售额,成交额,营收`，表达式 `SUM(pay_amount)`，主表 `fact_order`
- 订单量：别名 `订单数,单量`，表达式 `COUNT(*)`
- 退款单量：`COUNT(CASE WHEN status='refunded' THEN 1 END)`

口径会随问题一起送进模型，模型只负责“选表和拼条件”，不再自己猜“销售额含不含退款”。这是从“能写 SQL”到“查得准”的分界线。

## 安全护栏（agent/guard.py）

每次执行前都会过四道检查，任何一条不通过就不执行：

1. **只读**：解析 AST，出现 INSERT/UPDATE/DELETE/DDL/GRANT 等节点直接拒绝。
2. **单语句**：多语句、注释注入式拼接一律拒绝。
3. **表白名单**：只能访问当前数据源已自省出的业务表；`information_schema`、`pg_catalog` 等系统库直接拦。
4. **行数上限**：无 LIMIT 时按方言自动补 `LIMIT 200`（SQL Server 走 `TOP`，Oracle 走 `FETCH FIRST`），并截断超量返回。

## 目录

```
backend/
  app/
    main.py                应用装配（路由、异常映射、生命周期）
    core/                  配置、依赖单例、异常
    schemas/               catalog 结构与 API 入参
    db/                    URL 归一、schema 自省、只读执行、元数据仓储
    llm/                   OpenAI 兼容客户端、提示词
    agent/                 意图 / 召回 / 生成 / 护栏 / 执行自修 / 解释 / 多轮上下文 / 结构化记忆
    services/              数据源、问数编排、指标口径、会话记忆、导出
    api/                   路由汇总与 system / connections / query / metrics / conversations
  data/
    demo_shop.db          演示电商库
    platform.db           数据源、schema 目录、指标、日志、会话
    testdata/             运行时生成的15个合成库及manifest（不提交Git）
  scripts/
    seed_demo.py          生成演示库并注册数据源/指标
    generate_testdata.py  生成/刷新终测库并注册数据源
  .env                    模型配置（不随包分发）
  .env.example            配置示例
  requirements.txt        Python 依赖
  run.ps1                 Windows 启动脚本

frontend/
  index.html               Vite 入口
  vite.config.js           base=/static/，outDir=dist
  package.json
  src/
    App.vue                页面装配、数据源/会话/指标状态、问数与回放
    api.js                 请求封装、剪贴板、会话 localStorage
    style.css              全局样式
    components/            DataSourcePanel / ConversationPanel / SchemaPanel / MetricsPanel / ChatPanel / AnswerCard
  dist/                    npm run build 产物（由 FastAPI 静态托管）
```



## 已知边界

- 语义注记：SQLite 没有列注释，演示库用 `_schema_notes` 表补充中文语义；MySQL/PostgreSQL 直接读原生 COMMENT。
- 查询超时：当前靠 LIMIT 与行数上限控制，未做数据库侧 statement_timeout；生产环境建议在只读账号上配 `statement_timeout`。
- 无模型时不支持多表 JOIN 与复杂条件，只做单表聚合兜底。
- 平台元数据库存连接串明文（本地单机演示用）；上生产应改为密钥管理 + 加密存储。
