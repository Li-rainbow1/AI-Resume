# python-ai-backend

基于 FastAPI 的 Python AI 后端，提供邮箱注册与密码重置、加密登录、聊天、AI 面试、RAG、图片 OCR、会话存储，以及与前端共享的 Realtime client-secret 能力。

## 作用说明

- `/api/auth/*` 提供当前前端使用的认证契约。
- 注册、密码重置验证码通过管理员配置的 SMTP 邮箱发送，验证码摘要存储到 MySQL。
- 对外继续提供 `/api/ai/*` 风格接口。
- AI 面试会话与消息历史存储到 MySQL。
- RAG 向量检索使用 PostgreSQL + pgvector。
- AI 面试语音通过 `/api/ai/realtime/client-secret` 获取会话：OpenAI 使用临时密钥和 WebRTC，百炼/豆包使用一次性代理票据和 `/ws/ai/realtime-asr`；服务不可用时前端自动回退到浏览器语音识别。

## 快速启动

### 前置依赖

- Python `3.11+`
- `uv`
- MySQL
- PostgreSQL + pgvector
- Redis（Docker 部署实时语音代理时使用）
- 任一支持 SMTP 的邮箱及其授权码（需要注册和密码重置功能时）

### 1. 启动数据库并迁移

在仓库根目录执行：

```bash
docker compose --profile python-ai up -d mysql pgvector redis
docker compose --profile migration build flyway-mysql
docker compose --profile migration run --rm --no-deps flyway-mysql
docker compose --profile migration run --rm --no-deps flyway-pgvector
```

迁移文件位于 `sql/migrations/`，应用启动不会自动建表。需要本地演示账号时，再手工执行 `sql/seeds/mysql/local_demo_users.sql`。

### 2. 准备环境变量

复制 `python-ai-backend/.env.example` 为 `python-ai-backend/.env`，至少确认以下配置：

```bash
SERVER_PORT=8999
APP_CORS_ALLOWED_ORIGINS=http://localhost:5173
APP_AUTH_TOKEN_SECRET=replace_with_a_random_token_secret
APP_AUTH_EMAIL_CODE_SECRET=replace_with_a_separate_random_secret

MAIL_HOST=smtp.qq.com       # 也可以填写 smtp.163.com、smtp.gmail.com 等
MAIL_PORT=465               # 465（SSL）或 587（STARTTLS）
MAIL_SECURITY_MODE=ssl      # ssl、starttls 或 none；不填写时按端口兼容推导
MAIL_USERNAME=
MAIL_AUTHORIZATION_CODE=

OPENAI_API_KEY=
OPENAI_CHAT_MODEL=gpt-5.4

EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_BASE_URL=https://api.openai.com/v1
OPENAI_EMBEDDING_API_KEY=
OPENAI_EMBEDDING_MODEL=text-embedding-3-large

OPENAI_REALTIME_BASE_URL=https://api.openai.com
OPENAI_REALTIME_API_KEY=
OPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-4o-transcribe

# 可选：实时语音切换为百炼或豆包（网页保存的管理员配置优先）
REALTIME_PROVIDER=openai
REALTIME_PROXY_TICKET_REDIS_URL=  # 多进程/生产部署必须配置
REALTIME_PROXY_TICKET_ALLOW_MEMORY=false  # 仅单进程本地开发可改为 true
BAILIAN_ASR_WORKSPACE_ID=
BAILIAN_ASR_API_KEY=
BAILIAN_ASR_MODEL=qwen-audio-3.0-asr-flash-streaming
BAILIAN_ASR_HOTWORDS=
VOLCENGINE_ASR_APP_KEY=
VOLCENGINE_ASR_RESOURCE_ID=volc.seedasr.sauc.duration

MYSQL_DATASOURCE_URL=mysql+pymysql://root:root@127.0.0.1:3306/resume-builder
MYSQL_DATASOURCE_USERNAME=root
MYSQL_DATASOURCE_PASSWORD=root

PGVECTOR_DATASOURCE_URL=postgresql+psycopg://pgvector:pgvector@127.0.0.1:5433/resume-builder
PGVECTOR_DATASOURCE_USERNAME=pgvector
PGVECTOR_DATASOURCE_PASSWORD=pgvector
```

### 3. 安装依赖

在 `python-ai-backend/` 目录执行：

```bash
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
uv pip install --python .venv\Scripts\python.exe -r requirements-optional.txt
```

### 4. 启动后端

在仓库根目录执行：

```bash
start-python-backend.bat
```

备用方式：

```powershell
.\start-python-backend.ps1 -Port 8999
```

默认地址：`http://127.0.0.1:8999`

健康检查：

- `GET /health`
- `GET /health/runtime`

## 环境变量说明

模板文件：`python-ai-backend/.env.example`

当前保留的关键变量分组：

- 认证：`APP_AUTH_TOKEN_*`、`APP_AUTH_EMAIL_CODE_*`
- 系统服务配置中心：`APP_SYSTEM_CONFIG_ENCRYPTION_KEY`（AES-GCM 根密钥）
- 邮件：`MAIL_HOST`、`MAIL_PORT`、`MAIL_SECURITY_MODE`、`MAIL_USERNAME`、`MAIL_AUTHORIZATION_CODE`、`MAIL_*_TIMEOUT_MILLIS`
- Chat：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_CHAT_*`
- Embedding：`EMBEDDING_PROVIDER`、`OPENAI_EMBEDDING_*`、`OLLAMA_EMBEDDING_*`
- Vision OCR：`VISION_PROVIDER`（`openai` / `ollama`）、`OPENAI_VISION_*`；管理员页面选择 Ollama 后不需要 API Key，也不会发送 `detail`
- Realtime：`REALTIME_PROVIDER`、`OPENAI_REALTIME_*`、`BAILIAN_ASR_*`、`VOLCENGINE_ASR_*`
- Realtime 票据：`REALTIME_PROXY_TICKET_REDIS_URL`、`REALTIME_PROXY_TICKET_ALLOW_MEMORY`；Docker Compose 默认使用 Redis。未配置 Redis 时默认拒绝创建票据，只有单进程本地开发显式设为 `REALTIME_PROXY_TICKET_ALLOW_MEMORY=true` 才使用内存票据
- MySQL：`MYSQL_DATASOURCE_*`
- pgvector：`PGVECTOR_DATASOURCE_*`
- RAG：`RAG_CHUNK_SIZE`、`RAG_CHUNK_OVERLAP`、`RAG_MAX_FILE_SIZE_MB`

说明：

- `SERVER_PORT` 建议保持 `8999`，与前端代理一致。
- `APP_AUTH_TOKEN_SECRET` 与 `APP_AUTH_EMAIL_CODE_SECRET` 必须使用两段不同的随机密钥。
- `APP_SYSTEM_CONFIG_ENCRYPTION_KEY` 用于加密管理员填写的 AI API Key 和 SMTP 授权码。建议使用 32 字节随机值的 URL-safe Base64，首次部署时手工生成并单独备份；缺少它时仍可使用部署环境变量，但无法保存网页配置。
- `MAIL_HOST`、`MAIL_PORT` 和 `MAIL_SECURITY_MODE` 按发件邮箱服务商填写。加密方式支持 SSL、STARTTLS 和无加密；QQ、网易、Gmail、Outlook 等邮箱通常需要在邮箱设置中单独生成 SMTP 授权码，不能填写网页登录密码。
- `APP_INTERVIEW_RAG_TOP_K`、`APP_INTERVIEW_RAG_SIMILARITY_THRESHOLD`、`APP_INTERVIEW_RAG_TIMEOUT_SECONDS` 仅用于 AI 面试链路；`APP_INTERVIEW_CONTEXT_SOFT_CHARS`、`APP_INTERVIEW_CONTEXT_HARD_CHARS` 和 `APP_INTERVIEW_SUMMARY_CHARS` 使用字符预算控制长对话压缩，部署前应按实际模型上下文容量校准。
- OpenAI 实时转写仍使用官方 API 和浏览器 WebRTC；网页只配置识别模型与 API Key，Base URL、内部路径和语言提示由后端固定，语言交给 OpenAI 自动处理中文为主的中英混合语音。
- 选择百炼时填写 Workspace ID、API Key 和 `qwen-audio-3.0-asr-flash-streaming` 或 `fun-asr-realtime`；Qwen 默认发送 `zh`/`en` 双语提示，Fun 发送 `zh` 单语提示并使用热词、上下文增强英文专业词；热词一行一个，Qwen 作为即时词表发送，Fun 保存时自动同步预编译词表（权重固定为 4）。
- 选择豆包时只需填写新版控制台的 App Key；资源 ID、WebSocket 地址、PCM16/16kHz、中文为主的中英混合识别和默认 VAD 由后端固定。
- `/ws/ai/realtime-asr` 只接受短时一次性票据，前端不接触上游 API Key。百炼上下文自动携带最近面试消息（最多每种角色 5 条、每轮最多 400 字），页面不提供 VAD、超时或内部路径配置。
- Redis 票据只保存用户标识、供应商和脱敏后的文本上下文，不保存上游 API Key；票据通过原子读取并删除保证只能消费一次。

## API 摘要

认证接口：

- `GET /api/auth/login-key`
- `POST /api/auth/login`
- `POST /api/auth/email-code`
- `POST /api/auth/register`
- `POST /api/auth/password-reset/email-code`
- `POST /api/auth/password-reset`

AI 基础路径：`/api/ai`

- `POST /chat`
- `POST /chat/stream`
- `POST /interview/turn/stream`
- `GET /interview/sessions`
- `GET /interview/sessions/{sessionId}`
- `POST /rag/query`
- `POST /rag/documents`
- `POST /rag/upload`
- `POST /realtime/client-secret`

管理员系统服务配置（均要求 `role=admin`）：

- `GET /api/admin/system-services`
- `POST /api/admin/system-services/{serviceKey}/test`
- `PUT /api/admin/system-services/{serviceKey}`
- `DELETE /api/admin/system-services/{serviceKey}?expectedVersion=...`

管理员页面保存的 AI API Key 和 SMTP 授权码会使用 `APP_SYSTEM_CONFIG_ENCRYPTION_KEY` 加密后写入 MySQL；接口只返回脱敏状态（固定掩码和末四位），不会返回完整密钥。数据库覆盖配置按全局修订号动态刷新，Embedding 配置变更后旧向量会被索引标识隔离，需要重新上传资料，系统不会自动删除旧数据。

## 常见问题

- 后端启动失败时，先检查 `.env` 中的数据库连接和端口是否与容器一致。
- 验证码返回 `503` 时，检查 `APP_AUTH_EMAIL_CODE_SECRET`、SMTP 主机/端口、`MAIL_USERNAME` 和 `MAIL_AUTHORIZATION_CODE`；SMTP 授权码不要填网页登录密码。
- 浏览器提示获取实时语音会话失败时，检查当前供应商所需的 Workspace/API Key 或豆包 App Key；错误响应会脱敏，不会打印密钥。
- 浏览器语音自动回退到免费识别时，通常是代理票据、上游 WebSocket 或凭据异常；确认后端依赖已安装并查看 FastAPI 的通用错误日志。
