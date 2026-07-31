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

## 网页 JD 文本提取

`crawler` 分支提供一个独立的公开网页文本提取接口。它只抓取和清洗网页，
不会调用大模型，也不会自动进入现有 JD 解析或简历生成流程。

```bash
curl -X POST http://127.0.0.1:8000/jds/extract-url \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/jobs/software-engineer"}'
```

响应中的 `raw_text` 可以由调用方确认或修改，后续再提交给 `/jds/parse`。
第一版优先读取网页中的 `JobPosting` JSON-LD，否则提取可见的岗位正文。
如果静态网页没有正文，会自动启动 Playwright Chromium，等待 JavaScript
渲染后再次提取。成功时 `extraction_method` 为 `playwright_json_ld` 或
`playwright_html`。仅允许公开的 HTTP/HTTPS 网页；如果网站返回登录、
验证码或安全验证页面，接口会明确报错，不会绕过验证。

首次使用浏览器抓取前安装 Chromium：

```bash
uv sync
uv run playwright install chromium
```

本地如果已经安装 Google Chrome，代码会在专用 Chromium 不可用时自动使用
系统 Chrome。服务器环境通常没有系统 Chrome，因此部署时仍需执行上述安装。
Chromium 本地运行包不提交到 Git。部署 `crawler` 分支时也必须在部署环境
安装 Chromium 及其系统依赖；当前 `FastAPI` 分支的线上服务不包含该接口。

正式简历依次展示个人优势、工作经历、项目经历、荣誉奖项、教育背景和相关技能。
个人优势按岗位适配度排序，最多保留 6 条，并结合工作或项目事实佐证；
相关技能保留所有具有事实依据的候选人技能，即使已经在个人优势中出现也不会删除。
技能按照与 JD 的相关性排序，通用技能排在最后，并统一展示为“熟练度 + 技能名”的短语。
公司、职位和在职时间只取自解析后的原始事实。
荣誉奖项仅在原简历或用户补充信息中存在明确奖项事实时展示，奖项名称、
颁发方和时间不由模型编造。

## 本地启动

后端需要 Python 3.14 和 `DEEPSEEK_API_KEY`：

```bash
uv sync
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

免费 Render 服务休眠或重新部署时会清除本地运行数据。当前版本仍可完成
上传、生成、预览和立即下载，但保存的简历与解析 JSON 不保证长期保留。

## 测试

```bash
.venv/bin/python -m unittest discover -s app/test -p 'test*.py' -v
cd frontend
npm run lint
npm run build
```
