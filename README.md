# CiteMind

> 从文献出发，得到有原文证据、可量化评测的可信答案。

CiteMind 是一个面向研究生和科研人员的 RAG 个人知识库。它不仅实现“上传文档 → 检索 → 回答”，还保留页码、章节和原文证据，并通过 Recall@K、MRR 等指标验证检索效果。

- [完整使用手册](docs/USER_GUIDE.md)
- [开发与生产部署](docs/DEPLOYMENT.md)

## 立即体验

项目已启动时，打开 <http://localhost:3000>：

```text
demo@citemind.dev
CiteMind123!
```

登录后上传 [演示文档](docs/demo-paper.md)，即可测试问答、原文引用和检索评测。

## 功能

- JWT 注册、登录和知识库成员权限隔离
- PDF、DOCX、Markdown、TXT 上传与 SHA-256 去重
- Celery + Redis 异步解析、失败状态与任务重试
- 章节感知切分，保留文件、页码和章节元数据
- PDF 跨页页眉/页脚识别，过滤页码、匿名审稿声明和出版版权装饰文本
- PostgreSQL + pgvector 原生向量存储与 HNSW 近似索引，SQLite 零配置降级
- FastEmbed multilingual-e5、Hashing、OpenAI、Ollama 四种 Embedding 提供商
- 多语言稠密向量 + BM25 混合检索；生产模式并行召回向量候选与全文候选
- 基于 Reciprocal Rank 的稳定分数融合、原问题词覆盖、章节意图奖励与重复证据抑制
- 原始问题 BM25/词项覆盖/语义相关性门控：无直接证据时拒绝推断，避免扩展词制造假命中
- 文档级检索范围与一键重新索引
- 本地摘要与 OpenAI 兼容模型综合回答
- SSE 流式问答，结束事件携带引用、置信度与检索耗时
- 回答关联文件、页码、章节、原文和相关度
- 运行状态面板：数据库、任务模式、平均延迟与错误率
- 一键将问答证据加入评测集
- Recall@K、Precision@K、MRR、Hit Rate 和检索延迟
- Alembic 数据库迁移、Docker Compose 与自动化测试

## 架构

```text
Next.js 16 / React 19
          │ JWT
          ▼
       FastAPI ───────── PostgreSQL + pgvector
          │                         │
          ├── SSE 对话与权限          └── 向量与元数据过滤
          ├── 混合检索 / 评测
          ├── 请求指标 / 健康检查
          └── Celery ─ Redis ─ Document Worker
                               ├── pypdf / python-docx
                               ├── 章节感知切分
                               └── multilingual-e5 / OpenAI / Ollama Embedding
```

## Docker 启动（推荐）

需要 Docker Desktop：

```bash
cp .env.example .env
docker compose up --build
```

Compose 会启动：

- PostgreSQL 16 + pgvector
- Redis
- Alembic migration job
- FastAPI
- Celery Worker
- Next.js

访问：

- Web：http://localhost:3000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

演示账户：

```text
demo@citemind.dev
CiteMind123!
```

公开部署前必须修改 `JWT_SECRET_KEY`、数据库密码并关闭 `DEMO_SEED_ENABLED`。

## 无 Docker 本地开发

后端默认使用 SQLite、multilingual-e5-small 和 eager 任务，无需 PostgreSQL、Redis或模型密钥。首次建立索引会下载本地 ONNX 模型：

```bash
cd apps/api
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

```bash
cd apps/web
pnpm install
pnpm dev
```

Windows 已完成一次依赖安装后，可在项目根目录一键启动，无需手动输入 `pnpm`：

```powershell
cd C:\Users\Lun\Documents\项目
.\scripts\dev.ps1
```

关闭服务：

```powershell
.\scripts\stop-dev.ps1
```

如果模型或正文清洗规则升级，网页会把旧文档标记为“索引需更新”；点击该文档右侧的“重建索引”即可。免密钥翻译依赖 Google/MyMemory 的公网服务，网络不可达时页面会提供“重试”和“在 Google 翻译中打开”的降级入口。

## 使用真实 Embedding

默认配置已经使用支持中英跨语言检索的 `intfloat/multilingual-e5-small`（384 维）。模型切换或升级正文清洗规则后，在文档面板选择论文并点击“重建索引”。测试环境可设置 `EMBEDDING_PROVIDER=hashing` 避免下载模型，但不应将 Hashing 用于真实问答。

### OpenAI 兼容服务

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=384
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=your-key
```

### Ollama

```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768
EMBEDDING_BASE_URL=http://localhost:11434
```

向量维度改变后需要新建数据库迁移或重建开发数据库，模型切换后需要重新索引文档。

## 检索参数

生产模式通过 Alembic 自动创建 pgvector HNSW 索引和 PostgreSQL 全文索引。可按知识库规模调整：

```env
RETRIEVAL_CANDIDATE_K=40
PGVECTOR_HNSW_EF_SEARCH=80
```

`RETRIEVAL_CANDIDATE_K` 越大，进入融合排序的候选越多；`PGVECTOR_HNSW_EF_SEARCH` 越大，HNSW 召回通常越高但查询更慢。调参应以项目内 Recall@K、MRR 和延迟评测结果为依据。

## 使用远程生成模型

```env
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_CHAT_MODEL=gpt-4.1-mini
```

不配置密钥时，系统使用本地检索摘要，不会发送文档内容到外部服务。

## 测试与迁移

```bash
cd apps/api
pytest -q
alembic upgrade head
```

端到端测试覆盖：

- 登录与未授权访问
- 新用户权限隔离
- 文档上传、解析和索引
- 检索问答与引用
- 评测集、Recall@K 和 MRR

前端生产构建：

```bash
cd apps/web
pnpm build
```

## 目录

```text
apps/api/app/                 FastAPI 业务代码
apps/api/app/services/        解析、Embedding、检索、生成、评测
apps/api/migrations/          Alembic 迁移
apps/api/tests/               API 端到端测试
apps/web/                     Next.js 工作台
docs/demo-paper.md            可上传的演示文档
docker-compose.yml            完整生产形态本地编排
```

## 当前边界

- SQLite 模式以 JSON 保存向量并全量扫描，适合开发和测试；正式部署使用 pgvector HNSW 与全文候选召回。
- 扫描版 PDF 尚未接入 OCR。
- 当前融合排序采用稠密向量、BM25、原问题覆盖与章节意图规则；更大规模或高精度场景下一步可接 OpenSearch 和 Cross-Encoder Reranker。
- 评测标签由用户从问答引用中创建，后续可加入批量 CSV 导入和 nDCG。

## License

MIT
