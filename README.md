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

核心定制流程已经迁移为状态图：岗位匹配、初稿生成、事实审核、条件修订和
最终复核。现有 API 响应保持不变；单元测试使用内存 checkpointer。后续
human-in-the-loop 阶段会把 checkpointer 切换为 PostgreSQL，并在正式简历确认前
增加可恢复的 interrupt。

## 测试

```bash
.venv/bin/python -m unittest discover -s app/test -p 'test*.py' -v
cd frontend
npm run lint
npm run build
```
