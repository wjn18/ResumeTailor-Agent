# ResumeTailor-Agent

一款基于用户真实经历库，自动分析 JD、匹配能力、审核并改写简历的应用。

## 当前流程

1. 上传 PDF 或 DOCX 简历并解析候选人的技能事实。
2. 粘贴 JD 文本并解析岗位要求。
3. 可选：输入补充个人信息并合并到事实库。
4. 按事实匹配岗位要求，生成带 `source_fact_ids` 的初稿。
5. 逐句事实审核，自动修订有问题的内容，再次审核。
6. 组装正式简历，在网页中预览和修改。
7. 用户修改/确认后保存并下载 DOCX。

正式简历依次展示个人优势、工作经历、项目经历、教育背景和相关技能。
个人优势按岗位适配度排序，最多保留 6 条，并结合工作或项目事实佐证；
公司、职位和在职时间只取自解析后的原始事实，未在个人优势中展示的技术能力补充到相关技能。

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
