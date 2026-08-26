# CiteMind 使用手册

本手册分为“使用现成页面”和“自己启动项目”两部分。第一次体验建议先使用本地轻量模式，不需要大模型密钥。

## 1. 登录

打开 <http://localhost:3000>，使用演示账户：

```text
邮箱：demo@citemind.dev
密码：CiteMind123!
```

也可以点击“没有账户？立即注册”创建个人账户。不同账户只能看到自己有权限访问的知识库。

公开部署时应关闭演示账户，并修改数据库密码和 JWT 密钥。

## 2. 上传文档

进入工作台后，点击右上角“上传文档”或右侧“添加研究文档”。

支持：

- PDF
- DOCX
- Markdown
- TXT

单个文件最大 25 MB。系统会依次完成：

1. 计算 SHA-256，防止同一知识库重复上传；
2. 提取正文与页码；
3. 按章节和段落切分证据块；
4. 批量生成 Embedding；
5. 建立检索索引；
6. 将状态更新为“索引就绪”。

Docker 完整模式由 Celery Worker 异步处理；轻量模式会在 API 进程中直接完成。

如果更换了 Embedding 模型，先选中已有文档，再点击“重建索引”。

如果 PDF 是整页图片，当前版本可能无法提取文字，需要后续 OCR 模块。

## 3. 提问

文档显示“索引就绪”后，在底部输入框提问。例如：

```text
这篇论文解决了什么问题？
作者提出的方法包含哪些模块？
实验使用了哪些数据集和指标？
这项工作的主要局限是什么？
```

建议先在右侧选中目标论文；只有需要跨论文比较时才选择“全部文档”。系统会执行多语言语义向量与 BM25 混合检索，再返回：

- 通过 SSE 逐段显示的回答正文；
- 使用的证据数量；
- 检索耗时；
- 文档名和页码；
- 对应原文片段；
- 证据相关度。

点击引用卡片可以展开原文。重要结论仍应回到原文核查。

## 4. 创建检索评测集

得到回答后：

1. 展开一条正确引用；
2. 点击“设为评测相关证据”；
3. 系统会保存“问题—正确证据块”标签；
4. 多次提问并标注，形成一组基准问题；
5. 点击右侧“运行检索基准测试”。

评测面板显示：

- `Recall@K`：正确证据中有多少被 Top-K 检索结果召回；
- `Precision@K`：Top-K 中正确证据的比例；
- `MRR`：第一条正确证据排名的倒数均值；
- `Hit Rate`：至少命中一条正确证据的问题比例；
- 延迟：平均检索耗时。

简历中只能填写项目实际运行产生的评测数据，并同时说明问题数量、文档数量、Top-K 和标注方法。不要复制虚构指标，也不要使用只有一个问题的演示结果作为正式项目指标。

## 5. 模型运行模式

### 默认本地多语言模式

默认配置：

```env
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=intfloat/multilingual-e5-small
EMBEDDING_DIMENSIONS=384
OPENAI_API_KEY=
```

该模式不会向外部发送文档，支持中文问题检索中英文论文。回答是结构化证据摘要，不具备完整的大模型综合推理能力。首次运行需要从 Hugging Face 下载模型。

`EMBEDDING_PROVIDER=hashing` 仅用于自动化测试或离线检查流程，不建议用于真实论文检索。

### OpenAI 兼容 Embedding

在项目根目录创建 `.env`：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=384
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=你的密钥
```

### Ollama 本地 Embedding

```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768
EMBEDDING_BASE_URL=http://localhost:11434
```

更换 Embedding 模型后，已有文档必须点击“重建索引”。如果维度发生变化，pgvector 列维度也要通过数据库迁移同步修改。

### 大模型综合回答

```env
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_CHAT_MODEL=gpt-4.1-mini
```

后端也兼容实现了 OpenAI Chat Completions 接口的其他模型服务。

## 6. 查看运行状态

工作台右侧“运行状态”面板展示数据库后端、任务模式、平均请求耗时、错误率和已就绪文档数。本地轻量模式通常显示 `SQLite · 本地任务`，Docker 完整模式显示 `POSTGRESQL · Celery 异步`。

访问 <http://localhost:8000/health>，会返回：

```json
{
  "status": "ok",
  "model_mode": "local-extractive",
  "database_backend": "sqlite",
  "embedding_provider": "fastembed",
  "task_mode": "eager"
}
```

API 交互文档位于 <http://localhost:8000/docs>。

## 7. 常见问题

### 页面显示“无法连接 API”

确认 `http://localhost:8000/health` 可以访问，并检查 `.env` 中的 `CORS_ORIGINS`。

### 文档一直处于“处理中”

Docker 模式下检查 Worker 和 Redis：

```bash
docker compose ps
docker compose logs worker
docker compose logs redis
```

### 文档解析失败

先确认格式和文件大小。扫描版 PDF、加密 PDF、复杂跨页表格可能需要专门解析器。

### 回答没有引用

确认知识库中至少有一篇“索引就绪”的文档，并用文档中实际出现的概念重新提问。

### 登录状态失效

退出后重新登录。如果修改了 `JWT_SECRET_KEY`，原有 Token 会全部失效，这是正常现象。
