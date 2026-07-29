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

## 测试

```bash
.venv/bin/python -m unittest discover -s app/test -p 'test*.py' -v
cd frontend
npm run lint
npm run build
```
