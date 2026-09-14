# AI Resume Builder

面向求职场景的 AI 简历平台。支持简历编辑与多模板导出、AI 按模块优化、AI 模拟面试（含实时语音），以及基于 pgvector 的 RAG 知识库检索。

前端为 Vue 3 + TypeScript 单页应用，AI 能力由独立的 Python FastAPI 后端提供，通过 Docker Compose 一键部署。

![Vue 3](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11-009688?logo=fastapi&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8-4479a1?logo=mysql&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL%20%2B%20pgvector-17-4169e1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ed?logo=docker&logoColor=white)

## 功能特性

- **账号与权限**：注册、加密登录、邮箱验证码、密码重置、登录过期处理，以及基于角色的功能权限控制。
- **简历管理**：新建、切换、复制、重命名、删除，支持自动保存与手动保存。
- **简历编辑**：按模块编辑、显示隐藏、拖拽排序，编辑器与预览实时联动，适配移动端。
- **模板与导出**：内置 9 套简历模板，支持 PDF、Markdown、JSON 导出与 JSON 导入。
- **AI 优化**：按简历模块生成优化建议与优化内容，可直接应用到简历。
- **AI 面试**：候选人模拟面试与面试官追问两种模式，支持语音输入、会话历史和结束评分。
- **RAG 知识库**：上传文档或图片，经 OCR、结构化切块与 Embedding 后写入 pgvector；支持知识库分组、文档归档与面试会话的检索范围限定。
- **系统服务配置中心**：管理员在网页配置 AI 与邮件服务，密钥使用 AES-GCM 加密后入库。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Pinia、Vite、Tailwind CSS |
| AI 后端 | Python 3.11+、FastAPI、Uvicorn |
| 业务数据库 | MySQL 8（账号、简历、面试会话） |
| 向量数据库 | PostgreSQL 17 + pgvector（知识库向量） |
| 缓存与队列 | Redis（实时语音一次性票据、图片增强队列） |
| 对象存储 | MinIO（知识库原始文件，Bucket 私有） |
| 数据库迁移 | Flyway（由 Docker 临时容器执行） |
| AI 能力 | OpenAI 兼容的 Chat、Embedding、Vision OCR、Realtime |

## 目录结构

```text
.
├─ src/                       前端源码（Vue 3 + TypeScript）
│  ├─ api/                    后端接口封装
│  ├─ components/             业务组件（resume / ai / auth / settings）
│  ├─ services/               前端服务与 Prompt 模板
│  ├─ stores/                 Pinia 状态
│  └─ templates/              简历模板实现
├─ python-ai-backend/         Python AI 后端（FastAPI）
│  └─ app/                    api / application / domain / infrastructure 分层
├─ sql/
│  ├─ bootstrap/              手工建库脚本
│  ├─ migrations/             Flyway 版本迁移（mysql / postgresql）
│  └─ seeds/                  仅供本地导入的演示数据
├─ docker/flyway/             Flyway 迁移运行镜像
├─ .github/workflows/         CI/CD 工作流
├─ docker-compose.yml         Docker Compose 编排
├─ start-docker-python-ai.bat Windows 一键启动
└─ stop-docker-stack.bat      Windows 一键停止
```

## 快速开始

### 环境要求

- Docker Desktop（推荐，下列步骤均已容器化）
- 本地开发另需：Node.js `^20.19.0 || >=22.12.0`、Python 3.11+、[uv](https://github.com/astral-sh/uv)

### 方式一：Docker 一键启动（推荐）

1. 复制环境变量模板：

   ```powershell
   copy .env.docker.example .env
   ```

2. 编辑 `.env`，至少填写以下必需项（缺失时启动脚本会拒绝继续）：

   | 变量 | 说明 |
   | --- | --- |
   | `APP_SYSTEM_CONFIG_ENCRYPTION_KEY` | 系统服务配置中心的 AES-GCM 根密钥，32 字节随机值的 URL-safe Base64 |
   | `MINIO_ROOT_PASSWORD` | MinIO 管理员密码 |
   | `RAG_OBJECT_STORAGE_SECRET_KEY` | 知识库对象存储专用账号密钥 |
   | `OPENAI_*_API_KEY` | 对应能力的 API Key，也可稍后在系统服务配置中心由管理员填写 |
   | `MAIL_USERNAME` / `MAIL_AUTHORIZATION_CODE` | 邮箱验证码发信账号与授权码 |

   生成根密钥可在 PowerShell 中执行：

   ```powershell
   $bytes = [byte[]]::new(32)
   [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
   [Convert]::ToBase64String($bytes)
   ```

3. 一键启动（脚本会依次执行数据库迁移、MinIO 初始化、后端与前端启动，并等待健康检查通过）：

   ```powershell
   .\start-docker-python-ai.bat
   ```

4. 访问 `http://localhost:3000`（前端）与 `http://localhost:8999/docs`（后端接口文档）。

停止全部服务：

```powershell
.\stop-docker-stack.bat
```

### 方式二：本地开发

前端：

```powershell
npm install
npm run dev
```

后端（在 `python-ai-backend/` 目录下）：

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
uv pip install --python .venv\Scripts\python.exe -r requirements-optional.txt
uv run uvicorn app.main:app --host 0.0.0.0 --port 8999
```

本地开发前请先复制 `python-ai-backend/.env.example` 为 `python-ai-backend/.env` 并填写数据库、AI 与邮件配置。

## 配置说明

`.env.docker.example` 与 `python-ai-backend/.env.example` 是带注释的配置模板，涵盖以下分组：

- **基础服务**：端口、CORS 白名单。
- **认证与安全**：令牌密钥与有效期、邮箱验证码策略、系统服务配置加密根密钥。
- **数据库**：MySQL、PostgreSQL + pgvector 连接信息。
- **对象存储**：MinIO 端点、Bucket 与应用账号。
- **AI 能力**：Chat、Embedding、Vision OCR、Realtime 语音各自的地址、密钥与模型。
- **RAG 参数**：分块大小与重叠、单文件大小上限、面试检索的 TopK 与相似度阈值。

> 容器内的 `localhost` / `127.0.0.1` 指向容器自身。若 AI 服务或数据库运行在宿主机上，请改用 `host.docker.internal`；启动脚本会自动改写常见 URL。

## 数据库与迁移

- `sql/bootstrap/`：首次部署时手工建库。
- `sql/migrations/`：Flyway 版本迁移，MySQL 与 PostgreSQL 分目录维护，文件名遵循 `V<版本>__<描述>.sql`。
- `sql/seeds/`：仅供本地开发的演示管理员与普通账号，**生产环境禁止执行**。

启动脚本与 CI/CD 会先执行全部待处理迁移，成功后才拉起后端。迁移前会自动备份，详见 [SQL 说明](sql/README.md)。

## 端口约定

| 服务 | 默认端口 | 变量 |
| --- | --- | --- |
| 前端 | 3000 | `FRONTEND_PORT` |
| AI 后端 | 8999 | `BACKEND_PORT` / `SERVER_PORT` |
| MySQL | 3306 | `MYSQL_PORT` |
| PostgreSQL + pgvector | 5433 | `PGVECTOR_PORT` |
| MinIO API / 控制台 | 9000 / 9001 | `MINIO_API_PORT` / `MINIO_CONSOLE_PORT` |

## 常用命令

```powershell
npm run dev          # 启动前端开发服务器
npm run type-check   # TypeScript 类型检查
npm run lint         # ESLint 检查
npm run build        # 构建前端产物
```

## 说明

本仓库只保留项目源码、配置模板和文档。本地测试数据、运行报告、内部设计稿与运行环境文件不纳入版本控制，相关路径见 `.gitignore`。
