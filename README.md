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

`app/workflows/tailoring_runtime.py` 管理进程内任务和共享内存 checkpointer。
同一任务并发请求返回 409；已成功审核的重复请求复用结果，不重复调用模型或保存。
审核或保存失败后，可用原 `thread_id` 重试，从已完成的图节点继续；前端提供
“重试审核”按钮。返回补充信息时保留原输入。任务不存在或过期返回 404。

本阶段必须使用**单进程、单实例后端**（一个 Uvicorn worker）。任务在一小时无
请求后过期并按需清理；最多保留 256 个任务，达到上限返回 503。进程重启会丢失
未完成任务；已保存的简历仍在业务数据库中。浏览器刷新后恢复、跨实例执行、
PostgreSQL checkpointer、持久化任务调度和用户确认 interrupt 留待后续阶段。
用户编辑后的内容版本与复审绑定也尚未迁移到图内。

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
以及简历保存、修改、确认、DOCX 下载和重新编辑流程。未设置 `TEST_DATABASE_URL` 时跳过这些集成测试。
