# ResumeTailor-Agent

一款基于用户真实经历库，自动分析 JD、匹配能力、审核并改写简历的应用。

## 当前流程

1. 上传 PDF 或 DOCX 简历并解析候选人的技能事实。
2. 粘贴 JD 文本并解析岗位要求。
3. 可选：输入补充个人信息并合并到事实库。
4. 按事实匹配岗位要求，生成带 `source_fact_ids` 的初稿并立即在前端预览。
5. 后台逐句事实审核，自动修订有问题的内容，再次审核，并在前端分模块替换。
6. 组装正式简历，在网页中预览和修改。
7. 用户修改/确认后保存并下载 DOCX。

正式简历在姓名与目标岗位下方展示个人联系方式，正文依次展示教育经历、
个人优势、工作经历、项目经历、荣誉奖项和相关技能。教育经历与个人联系方式
都以带独立 ID 和 `facts` 的结构保存；旧版 `education`、`email`、`phone`
字段会自动转换，已有解析结果仍可读取。
个人优势按岗位适配度排序，最多保留 6 条，并结合工作或项目事实佐证；
相关技能保留所有具有事实依据的候选人技能，即使已经在个人优势中出现也不会删除。
技能按照与 JD 的相关性排序，通用技能排在最后，并统一展示为“熟练度 + 技能名”的短语。
公司、职位和在职时间只取自解析后的原始事实。
荣誉奖项仅在原简历或用户补充信息中存在明确奖项事实时展示，奖项名称、
颁发方和时间不由模型编造。

## 本地启动

后端需要 Python 3.14、PostgreSQL、`DATABASE_URL` 和 `DEEPSEEK_API_KEY`：

```bash
uv sync
export DATABASE_URL="postgresql://resume_tailor:password@localhost:5432/resume_tailor"
uv run python -m databae.init_database
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

在另一个终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

打开 [http://127.0.0.1:3000](http://127.0.0.1:3000)。

如需修改后端地址，复制 `frontend/.env.example` 为
`frontend/.env.local` 并修改 `NEXT_PUBLIC_API_URL`。

## LLM 配置与切换

简历解析、JD 解析、补充事实、匹配、改写和审核统一通过
`app/services/model_client.py` 的 `LLMJSONClient.request_json()` 调用模型。
默认 `LLM_PROVIDER=deepseek`，已有 `DEEPSEEK_*` 环境变量继续有效。
同名的 `LLM_*` 配置优先于当前厂商的 `DEEPSEEK_*`、`ANTHROPIC_*` 或 `GEMINI_*`。

| `LLM_PROVIDER` | 协议 | 默认接口 |
| --- | --- | --- |
| `deepseek` | Chat Completions，附带 DeepSeek 默认参数 | DeepSeek 官方接口 |
| `openai_compatible` | Chat Completions | 必须设置 `LLM_API_URL` |
| `anthropic` | Claude Messages 原生接口 | `https://api.anthropic.com/v1/messages` |
| `gemini` | Gemini generateContent 原生接口 | Google 官方接口，根据模型名拼接 |

除旧版 DeepSeek 默认模型外，其他模式均需设置 `LLM_MODEL`，使用账号实际可用的模型 ID。

切换到提供 Chat Completions 兼容接口的服务，只需设置环境变量并重启后端：

```powershell
$env:LLM_PROVIDER="openai_compatible"
$env:LLM_API_URL="https://your-provider.example/v1/chat/completions"
$env:LLM_MODEL="your-model-id"
$env:LLM_API_KEY="your-api-key"
$env:LLM_MAX_TOKENS="16384"
```

`LLM_API_URL` 填完整请求地址，不会自动补 `/chat/completions`。
通用模式必须填写地址和模型；密钥可留空以连接无需认证的本地服务，
且不会回退读取 DeepSeek 密钥。`.env.example` 是配置参考；应用不会自动加载
根目录 `.env`，可导出环境变量或使用 Uvicorn 的 `--env-file .env`。

使用 Claude 原生接口：

```powershell
$env:LLM_PROVIDER="anthropic"
$env:LLM_MODEL="你的 Claude 模型 ID"
$env:LLM_API_KEY="你的 Anthropic API Key"
$env:LLM_TEMPERATURE="omit"
Remove-Item Env:LLM_API_URL -ErrorAction SilentlyContinue
```

使用 Gemini 原生接口：

```powershell
$env:LLM_PROVIDER="gemini"
$env:LLM_MODEL="你的 Gemini 模型 ID"
$env:LLM_API_KEY="你的 Gemini API Key"
$env:LLM_TEMPERATURE="omit"
Remove-Item Env:LLM_API_URL -ErrorAction SilentlyContinue
```

也可以分别用 `ANTHROPIC_API_KEY`、`GEMINI_API_KEY` 配置厂商密钥，但需清除已有
`LLM_API_KEY` 才会回退读取。切换厂商时同步更新密钥，并清除不适用的
`LLM_API_URL` 和 `LLM_EXTRA_BODY`，避免旧覆盖值继续生效。
Gemini 自定义接口地址可包含 `{model}` 占位符，也可填完整 `:generateContent` 地址。

不同模型的可选能力通过以下设置调整：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `LLM_JSON_MODE` | `true` | Chat Completions 发送 `response_format`，Gemini 发送 `responseMimeType`；设为 `false` 可关闭 |
| `LLM_TEMPERATURE` | `0.1` | 不支持温度参数时设为 `omit`；JSON 重试时增加 0.1，Claude 最高为 1，其他模式最高为 2 |
| `LLM_TOKEN_PARAMETER` | `max_tokens` | 仅 Chat Completions 使用，可改为 `max_completion_tokens`；原生协议自动映射 |
| `LLM_MAX_TOKENS` | `16384` | 按模型支持的输出上限设置 |
| `LLM_EXTRA_BODY` | 非 DeepSeek 模式为 `{}` | JSON 对象，加入厂商扩展参数；不能覆盖标准字段 |

DeepSeek 模式默认附加 `thinking={"type":"disabled"}`，可通过
`LLM_EXTRA_BODY={}` 清除；通用模式不会自动发送此参数。
客户端统一处理 HTTP 错误、JSON 提取和有限重试，业务层继续做结构与事实校验。
Claude 当前通过提示词请求 JSON，并做本地解析校验，不发送 `response_format`；
`LLM_JSON_MODE` 对 Claude 不生效。Gemini 的额外生成选项放在
`LLM_EXTRA_BODY` 的 `generationConfig` 对象中。

原生协议参考 [Claude Messages 文档](https://platform.claude.com/docs/en/api/messages/create)
和 [Gemini generateContent 文档](https://ai.google.dev/api/generate-content?hl=en)。
此入口覆盖上述三种协议的文本 JSON 请求；其他协议、云平台专属认证、工具调用和
多模态请求需要继续扩展，不能保证任意 LLM 即插即用。
切换模型后仍需验证实际 JSON 输出质量与参数支持情况。

## Netlify + Render 部署

部署固定使用 GitHub 的 `FastAPI` 分支。`render.yaml` 负责 FastAPI，
`netlify.toml` 负责 `frontend` 目录中的 Next.js。

1. 将 `FastAPI` 分支推送到 GitHub。
2. 登录 Render，选择 **New > Blueprint**，连接
   `wjn18/ResumeTailor-Agent`，确认 Blueprint branch 是 `FastAPI`。
3. 创建服务时填写 `DEEPSEEK_API_KEY`，等待后端部署完成并记录
   `https://...onrender.com` 地址。访问该地址的 `/health`，确认返回
   `{"status":"ok"}`。
4. 打开已创建的 Netlify 项目
   `https://app.netlify.com/projects/resume-tailor-agent`，连接同一个
   GitHub 仓库，将 Production branch 设置为 `FastAPI`。
5. Netlify 会读取 `netlify.toml` 中已配置的 Render 后端地址，直接部署。
6. 打开 `https://resume-tailor-agent.netlify.app` 测试完整生成流程。

解析后的简历、JD 和定制简历以 PostgreSQL JSONB 文档持久化。上传文件和
生成的 DOCX 是临时文件；DOCX 下载接口会根据数据库中的正式简历重新生成文件。

## 存储架构与数据库切换

业务调用链为 `api / services → services.database → Storage → 具体适配器`：

- `app/storage/base.py` 定义存储接口，覆盖通用 CRUD、JSON 文档读写和定制简历保存、列表查询。
- `app/storage/postgres.py` 和 `postgres_schema.py` 集中管理运行时 SQL、PostgreSQL 驱动、JSONB 转换与建表语句。
- `app/storage/factory.py` 根据 `STORAGE_BACKEND` 和 `DATABASE_URL` 创建并复用适配器。应用启动与旧的 `databae.init_database` 命令共用初始化入口。
- API 只处理数据库无关的 `StorageConflictError`；适配器将驱动完整性异常转换为该异常，对外仍返回 HTTP 409。其他驱动异常转换为 `StorageError`，原异常保留在异常链中。

`STORAGE_BACKEND` 默认是 `postgresql`，现有部署无需增加配置。
当前生产适配器只有 PostgreSQL；更换 PostgreSQL 实例时修改 `DATABASE_URL` 并迁移已有数据即可。
接入 MySQL、SQLite 等新类型时，实现 `Storage` 的全部方法（包含初始化和异常转换），
在工厂的 `BACKENDS` 中注册一个接收连接字符串的构造函数，再设置对应的 `STORAGE_BACKEND`。
业务接口和简历处理流程无需随适配器修改，但数据库结构及已有数据的跨库迁移仍需单独处理。
新适配器应返回普通字典和 JSON 值，不向业务层暴露连接、游标或驱动异常。

`databae.migrate_to_postgres` 是专用于旧 SQLite/JSON 数据的 PostgreSQL 迁移工具，
始终使用 PostgreSQL 适配器，不随应用的 `STORAGE_BACKEND` 切换。

## 从 SQLite 和 JSON 文件迁移

设置目标 PostgreSQL 的 `DATABASE_URL` 后执行：

```bash
uv run python -m databae.migrate_to_postgres
```

脚本会导入旧的 `databae/resume_tailor.sqlite3`，以及原先保存在
`app/data/resumes`、`app/data/job_descriptions` 和
`app/data/tailored_resumes` 中的 JSON 文件。迁移脚本可重复执行；已有主键记录
不会重复创建，文档记录会按业务 ID 更新。

## LangGraph Phase 1

完整生成和前端的分阶段生成共用 `app/workflows/tailoring_graph.py`：岗位匹配、
初稿生成、事实审核、最多两次修订，以及明确的通过/待处理出口。原 Python
服务函数保留兼容入口，但编排逻辑统一委托给图。初审通过或修订没有改变草稿时，
复用已有审核结果，避免重复调用模型。

分阶段接口协议已更新，前后端需要一起升级：

1. `POST /tailoring/build/initial` 接收 `{jd, resume}`，执行至初稿节点后暂停，
   返回 `thread_id`、`status: initial_ready`、匹配报告、初稿和预览。
2. `POST /tailoring/build/review` 只接收 `{thread_id}`，从服务端检查点接续审核；
   不再接收客户端回传的 JD、简历、匹配报告或草稿。
3. 审核结果包含 `status`、`revision_count` 和最终审核报告。全部通过时为
   `awaiting_confirmation`，并保存待确认简历；修订达到上限仍有问题时为
   `needs_attention`、`saved_resume: null`，前端展示问题并禁止确认导出。
4. `/tailoring/build` 一次执行同一张图，返回相同的审核状态和保存规则。

## LangGraph Phase 2：持久化任务与恢复

前端现在使用持久化任务接口，在创建任务后立即取得 ID，通过查询展示初稿及审核结果。
原 `/build`、`/build/initial`、`/build/review` 接口继续可用，与后台执行器共用执行逻辑。

| 接口 | 行为 |
| --- | --- |
| `POST /tailoring/tasks` | 接收 `{jd, resume, request_id?}`，持久化输入快照并返回 202；`request_id` 为可选 UUID，同 ID、同输入重试返回原任务，不同输入返回 409。 |
| `GET /tailoring/tasks/{thread_id}` | 查询状态、当前/失败节点、修订次数、草稿/审核版本、初稿预览与最终结果；不存在返回 404。 |
| `POST /tailoring/tasks/{thread_id}/resume` | 将失败或停在初稿的任务重新排队，返回 202；已运行或完成的任务不重复执行，已取消的任务返回 409。 |
| `POST /tailoring/tasks/{thread_id}/cancel` | 排队/失败任务直接取消；正在执行的任务标记为 `cancelling`，在当前模型调用结束后的节点边界停止；进入最终保存阶段后返回 409。 |

任务状态包括 `queued`、`running`、`initial_ready`、`saving`、`awaiting_confirmation`、
`needs_attention`、`failed`、`cancelling` 和 `cancelled`。确认后的简历在查询时显示
`completed`；修改后的预览读取业务库最新内容，不会被生成时的旧快照覆盖。
前端把任务 ID 保存在当前标签页的 `sessionStorage` 中，刷新同一标签页后自动接续查询。
初稿仍会先显示；失败时可以重试，运行中可以取消。任务创建前的文件/JD 解析尚不支持恢复；
尚未保存的网页编辑也不会自动保存。

- `app/storage/workflow_factory.py` 独立管理任务库配置，默认使用 `DATABASE_URL`，
  也可以通过 `CHECKPOINT_DATABASE_URL` 指定单独的 PostgreSQL。更换业务数据库类型时，
  任务库仍需 PostgreSQL；任务记录和检查点必须始终指向同一个任务库。
- 应用 lifespan 建立/关闭连接池、执行 LangGraph `PostgresSaver.setup()` 和任务表初始化。
  初始化用数据库锁串行化，支持多个进程同时启动；数据库账号需要建表和建索引权限。
- 每个进程默认启动两个后台执行器，`TAILORING_WORKERS` 可设置为 1–4。多进程/多实例
  通过 PostgreSQL session advisory lock 避免同一任务同时提交进度；锁与 checkpointer
  绑定在同一连接，丢失锁连接的旧执行器无法继续写检查点。
- 节点结果以 `durability="sync"` 保存。进程退出后，其他执行器会自动接手遗留的
  `queued/running/saving/cancelling` 任务；模型或业务错误记为 `failed`，等待用户重试，
  不无限自动重试。崩溃时尚未完成检查点写入的节点可能重新调用模型。
- 输入快照及哈希、流程版本、草稿/审核版本和失败节点保存在 `tailoring_runs` 中。
  简历结果使用任务对应的固定 ID，并通过业务存储的 `create_tailored_resume_document`
  原子插入一次；即使保存成功后丢失任务回执，重放也不会新建第二份简历或覆盖用户修改。
- PostgreSQL 不使用第一阶段的一小时内存过期策略；任务和检查点持续保留。部署多个
  Uvicorn worker 时，每个进程最多使用 8 个任务库连接，需按数据库连接额度配置实例数。
  使用连接代理时必须支持会话锁，不能使用 transaction pooling 模式。

运行时内存适配器仅用于显式构造的测试。生产启动若没有可用的 PostgreSQL 会失败，
不会降级为内存存储。当前前端使用任务查询轮询，暂未接入实时事件推送。

## LangGraph Phase 3：人工确认与正文版本审核

生产任务使用流程版本 3。生成和审核后，图通过 `interrupt()` 暂停在
`human_decision` 节点；等待用户期间不占用执行器或数据库连接。用户操作持久化后，
执行器用同一任务 ID 和 `Command(resume={interrupt_id: decision})` 接续。
接口实现遵循 [LangGraph interrupt 文档](https://reference.langchain.com/python/langgraph/types/interrupt)。

```mermaid
flowchart LR
    A[生成与事实审核] --> B[等待用户操作]
    B -->|编辑| C[正文版本加一]
    C --> D[完整正文复审]
    D --> B
    B -->|确认已审核版本| E[确认并允许导出]
    E --> B
```

`POST /tailoring/tasks/{thread_id}/decision` 返回 202，接收以下字段：

| 字段 | 规则 |
| --- | --- |
| `request_id` | 本次操作的 UUID；相同 ID 和内容重试返回任务当前状态，复用 ID 改变内容返回 409。 |
| `action` | `edit` 或 `confirm`。 |
| `expected_version` | 用户正在查看的 `content_version`；与服务器版本不符返回 409。 |
| `formal_resume` | 仅 `edit` 必填；`confirm` 禁止携带正文或客户端审核结果。 |

任务查询新增 `content_version`、`reviewed_content_version`、`content_hash`、
`reviewed_content_hash`、`current_document` 和 `pending_decision`。这些字段描述正式正文，
与自动生成阶段的 `draft_version`、`audit_version` 分开；`current_document` 支持复审失败或
页面刷新后恢复用户已提交的内容。`pending_decision` 包含待操作版本及能否确认。

- 保存编辑后产生新正文版本，旧审核立即失效。复审覆盖完整正式简历，包括姓名、联系方式、
  标题、教育、工作、项目、荣誉和技能；一条工作/教育等结构化记录连同其字段整体审核，
  避免将原有事实挪到另一家公司后仍视为通过。依据只来自任务原始输入，遗漏的审核条目按未通过处理。
- 用户编辑不会触发自动改写；复审未通过时保留原文并展示问题，允许继续修改。通过后回到
  `awaiting_confirmation`，必须再次确认才能变为 `completed`。已确认的任务仍可进入下一轮编辑。
- 前端提供“保存并重新审核”和“确认并导出”，编辑中禁用确认，展示当前正文版本。
  只有图中确认状态、正文版本及审核哈希一致时才允许下载 DOCX；排队、执行、失败、
  取消或再次编辑都会阻止下载。文件使用正文哈希区分版本。
- 编辑和确认共用任务执行锁。操作回执及 interrupt ID 持久化，崩溃后不会把同一操作重复应用
  到下一个人工节点。保存结果按 `(正文版本, 是否确认)` 原子递增，旧执行器不能覆盖新结果。
  检查点和任务 SQL 共用连接锁，防止并发 pipeline 操作干扰同一数据库会话。
- Phase 2 任务仍可恢复生成；其结果通过 `/resume` 升级到新版流程，保留最新已保存正文，
  重新审核后等待确认。前端恢复旧任务时自动执行升级。升级意图先持久化，支持中途重启。
- 旧 `/saved/{id}/confirm` 不再直接确认任意正文，返回 409；任务生成的正文也不能通过旧
  `/content` 接口绕过复审。没有任务记录的历史 `/save` 文档可以查看和编辑，但需重新生成
  才能获得可确认的审核记录。`/save` 上传的报告不作为导出授权依据。

本阶段没有新增环境变量；仍需现有的 PostgreSQL 任务库。未提交的网页编辑不会自动保存。

## 测试

```bash
.venv/bin/python -m unittest discover -s app/test -p 'test*.py' -v
cd frontend
npm run lint
npm run build
```

Windows 后端测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s app/test -p 'test*.py' -v
```

默认测试覆盖适配器选择、业务层解耦和异常映射，不需要数据库。
要同时运行真实 PostgreSQL 集成测试，先设置独立测试数据库的连接字符串：

```powershell
$env:TEST_DATABASE_URL = "postgresql://test_user:password@localhost:5432/resume_tailor_test"
.\.venv\Scripts\python.exe -m unittest discover -s app/test -p 'test*.py' -v
```

集成测试每例创建独立随机 schema，结束后删除该 schema；测试账号需要创建 schema 的权限。
覆盖六张业务表的 CRUD、约束冲突与回滚、级联删除、JSON 文档覆盖写入、定制简历排序，
以及简历保存、修改、确认、DOCX 下载和重新编辑流程。任务集成测试还覆盖真实进程崩溃后的
自动恢复、跨连接互斥、锁连接失效、取消、失败节点重试、幂等创建及保存回执丢失后的重放。
人工操作测试覆盖 interrupt 重启恢复、编辑审核中进程崩溃、确认回执丢失、旧版本冲突、
重复操作、过期保存写入拦截、历史任务升级，以及未通过审核时无法确认或导出。
测试账号还需要终止其自身测试连接的权限，以验证锁连接断开。
未设置 `TEST_DATABASE_URL` 时跳过这些集成测试。
