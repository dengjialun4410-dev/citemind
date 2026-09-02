# 部署说明

## 开发模式

开发模式使用 SQLite、Hashing Embedding 和 eager 文档任务：

```powershell
Copy-Item .env.example .env
./scripts/dev.ps1
```

停止由脚本启动的服务：

```powershell
./scripts/stop-dev.ps1
```

## Docker 完整模式

安装 Docker Desktop 后：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

首次启动会自动执行 Alembic 迁移，然后启动 API 和 Worker。

查看状态：

```powershell
docker compose ps
docker compose logs -f api
docker compose logs -f worker
```

停止容器：

```powershell
docker compose down
```

该命令不会删除数据库卷。只有明确不再需要数据时才使用 `docker compose down -v`。

## 生产部署检查表

上线前至少完成：

- 将 `JWT_SECRET_KEY` 替换为高强度随机值；
- 修改 PostgreSQL 密码；
- 设置 `DEMO_SEED_ENABLED=false`；
- 只允许实际前端域名访问 CORS；
- 使用 HTTPS；
- 不在浏览器或 Git 仓库中存储模型密钥；
- 为上传目录和 PostgreSQL 配置持久化备份；
- 在反向代理层设置请求大小、超时和限流；
- 增加恶意文件扫描；
- 为 API、Worker、Redis 和 PostgreSQL 配置监控告警。

示例生产环境变量：

```env
POSTGRES_PASSWORD=使用密码管理器生成的密码
JWT_SECRET_KEY=至少32字节的随机值
DEMO_SEED_ENABLED=false
CORS_ORIGINS=https://你的域名
CELERY_TASK_ALWAYS_EAGER=false
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=由部署平台注入
RETRIEVAL_CANDIDATE_K=40
PGVECTOR_HNSW_EF_SEARCH=80
OPENAI_API_KEY=由部署平台注入
```

## 数据库迁移

每次部署新版本前执行：

```bash
cd apps/api
alembic upgrade head
```

升级前应先备份生产数据库。不要在生产环境直接使用 `Base.metadata.drop_all()` 或删除数据库卷。

## GitHub 发布流程

本地完成检查后：

```powershell
./scripts/check.ps1
git status
git push -u origin codex/citemind-mvp
```

推送后 GitHub Actions 会再次运行后端测试和前端生产构建。
