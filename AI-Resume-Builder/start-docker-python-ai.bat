@echo off
setlocal EnableExtensions

REM 先停止当前 Python AI 套件，避免重复启动。
set "COMPOSE_ENV="
set "COMPOSE_ENV_FILE=.env"
if exist ".env" (
  echo [INFO] Using .env for Docker Compose variables.
) else if exist ".env.docker" (
  set "COMPOSE_ENV=--env-file .env.docker"
  set "COMPOSE_ENV_FILE=.env.docker"
  echo [INFO] Using .env.docker for Docker Compose variables.
) else (
  set "COMPOSE_ENV_FILE="
  echo([ERROR] 未找到 .env 或 .env.docker。MinIO 凭证必须显式配置后才能启动。
  exit /b 1
)

call :load_env "%COMPOSE_ENV_FILE%"
call :normalize_host_urls

docker compose %COMPOSE_ENV% --profile python-ai down
if errorlevel 1 exit /b %errorlevel%

call :ensure_build_images
if errorlevel 1 exit /b %errorlevel%

set "DB_SERVICES="
call :is_port_listening %MYSQL_PORT%
if not errorlevel 1 (
  echo [WARN] Host port %MYSQL_PORT% is already in use. Skipping MySQL container.
  echo [INFO] Python AI backend will use host.docker.internal:%MYSQL_PORT%.
  set "PYTHON_MYSQL_DATASOURCE_URL=mysql+pymysql://%MYSQL_DATASOURCE_USERNAME%:%MYSQL_DATASOURCE_PASSWORD%@host.docker.internal:%MYSQL_PORT%/%MYSQL_DATABASE%"
  call :is_compose_mysql_url FLYWAY_MYSQL_URL
  if not errorlevel 1 set "FLYWAY_MYSQL_URL=jdbc:mysql://host.docker.internal:%MYSQL_PORT%/%MYSQL_DATABASE%?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true"
  if "%FLYWAY_MYSQL_URL%"=="" set "FLYWAY_MYSQL_URL=jdbc:mysql://host.docker.internal:%MYSQL_PORT%/%MYSQL_DATABASE%?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true"
) else (
  set "DB_SERVICES=%DB_SERVICES% mysql"
  if "%FLYWAY_MYSQL_URL%"=="" set "FLYWAY_MYSQL_URL=jdbc:mysql://mysql:3306/%MYSQL_DATABASE%?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true"
)

if "%PYTHON_PGVECTOR_DATASOURCE_URL%"=="" if not "%PGVECTOR_DATASOURCE_URL%"=="" set "PYTHON_PGVECTOR_DATASOURCE_URL=%PGVECTOR_DATASOURCE_URL%"
call :is_external_pgvector_url PYTHON_PGVECTOR_DATASOURCE_URL
if not errorlevel 1 (
  echo [INFO] Using configured external or host pgvector URL. Skipping pgvector container.
  call :validate_python_external_postgres_migration
  if errorlevel 1 exit /b 1
  call :disable_pgvector_service
) else (
  call :is_port_listening %PGVECTOR_PORT%
  if not errorlevel 1 (
    echo [WARN] Host port %PGVECTOR_PORT% is already in use. Skipping pgvector container.
    echo [INFO] Python AI backend will use host.docker.internal:%PGVECTOR_PORT%.
    if "%PYTHON_PGVECTOR_DATASOURCE_URL%"=="" set "PYTHON_PGVECTOR_DATASOURCE_URL=postgresql+psycopg://%PGVECTOR_DATASOURCE_USERNAME%:%PGVECTOR_DATASOURCE_PASSWORD%@host.docker.internal:%PGVECTOR_PORT%/%POSTGRES_DB%"
    call :is_compose_pgvector_url PYTHON_PGVECTOR_DATASOURCE_URL
    if not errorlevel 1 set "PYTHON_PGVECTOR_DATASOURCE_URL=postgresql+psycopg://%PGVECTOR_DATASOURCE_USERNAME%:%PGVECTOR_DATASOURCE_PASSWORD%@host.docker.internal:%PGVECTOR_PORT%/%POSTGRES_DB%"
    call :is_compose_pgvector_url FLYWAY_POSTGRES_URL
    if not errorlevel 1 set "FLYWAY_POSTGRES_URL=jdbc:postgresql://host.docker.internal:%PGVECTOR_PORT%/%POSTGRES_DB%"
    if "%FLYWAY_POSTGRES_URL%"=="" set "FLYWAY_POSTGRES_URL=jdbc:postgresql://host.docker.internal:%PGVECTOR_PORT%/%POSTGRES_DB%"
    call :disable_pgvector_service
  ) else (
    set "DB_SERVICES=%DB_SERVICES% pgvector"
    if "%FLYWAY_POSTGRES_URL%"=="" set "FLYWAY_POSTGRES_URL=jdbc:postgresql://pgvector:5432/%POSTGRES_DB%"
  )
)

if "%FLYWAY_MYSQL_USER%"=="" set "FLYWAY_MYSQL_USER=%MYSQL_DATASOURCE_USERNAME%"
if "%FLYWAY_MYSQL_PASSWORD%"=="" set "FLYWAY_MYSQL_PASSWORD=%MYSQL_DATASOURCE_PASSWORD%"
if "%FLYWAY_POSTGRES_USER%"=="" set "FLYWAY_POSTGRES_USER=%PGVECTOR_DATASOURCE_USERNAME%"
if "%FLYWAY_POSTGRES_PASSWORD%"=="" set "FLYWAY_POSTGRES_PASSWORD=%PGVECTOR_DATASOURCE_PASSWORD%"

call :ensure_database_images
if errorlevel 1 exit /b %errorlevel%

REM 实时语音代理使用 Redis 保存跨进程一次性票据，必须先于后端启动并通过健康检查。
call :ensure_image REDIS_IMAGE
if errorlevel 1 exit /b %errorlevel%

echo [INFO] Starting Redis for realtime speech proxy tickets.
docker compose %COMPOSE_ENV% --profile python-ai up -d redis
if errorlevel 1 exit /b %errorlevel%
call :wait_for_healthy resume-builder-redis 120
if errorlevel 1 exit /b %errorlevel%

if not "%DB_SERVICES%"=="" (
  echo [INFO] Starting database containers:%DB_SERVICES%
  docker compose %COMPOSE_ENV% --profile python-ai up --build -d %DB_SERVICES%
  if errorlevel 1 exit /b %errorlevel%
  call :wait_for_started_databases
  if errorlevel 1 exit /b %errorlevel%
) else (
  echo([INFO] 未启动 Docker 数据库容器，将迁移已配置的外部数据库。
)

call :run_database_migrations
if errorlevel 1 exit /b %errorlevel%

REM 私有知识库文件存储必须先完成 Bucket 和专用账号初始化，后端才允许启动。
echo([INFO] 正在启动 MinIO 知识库文件存储。
docker compose %COMPOSE_ENV% --profile python-ai up -d minio minio-init
if errorlevel 1 exit /b %errorlevel%
call :wait_for_healthy resume-builder-minio 120
if errorlevel 1 exit /b 1
call :wait_for_completed resume-builder-minio-init 120
if errorlevel 1 exit /b 1

echo([INFO] 正在使用 Docker 启动 Python AI 后端。
docker compose %COMPOSE_ENV% --profile python-ai up --build -d --no-deps python-ai-backend
if errorlevel 1 exit /b %errorlevel%
call :wait_for_http http://127.0.0.1:%BACKEND_PORT%/health 60
if errorlevel 1 exit /b 1

echo([INFO] 正在使用 Docker 启动前端。
docker compose %COMPOSE_ENV% --profile python-ai up --build -d --no-deps resume-builder
if errorlevel 1 exit /b %errorlevel%
call :wait_for_http http://127.0.0.1:%FRONTEND_PORT%/ 60
if errorlevel 1 exit /b 1

echo.
echo([INFO] Python AI 后端和前端 Docker 服务已启动。
echo([INFO] 前端：http://localhost:%FRONTEND_PORT%
echo [INFO] Health: http://localhost:%BACKEND_PORT%/health
echo([INFO] 数据库迁移目录：sql/migrations/mysql 与 sql/migrations/postgresql。
echo.
docker compose %COMPOSE_ENV% --profile python-ai ps
echo.
docker compose %COMPOSE_ENV% logs --tail=80 python-ai-backend
if errorlevel 1 exit /b %errorlevel%

echo.
echo([INFO] 前端和后端均已就绪，可直接访问上面的地址。
exit /b 0

:load_env
set "FRONTEND_PORT=3000"
set "BACKEND_PORT=8999"
set "BACKEND_PORT_SET=0"
set "SERVER_PORT=8999"
set "MYSQL_PORT=3306"
set "MYSQL_DATABASE=resume-builder"
set "MYSQL_ROOT_PASSWORD=root"
set "MYSQL_DATASOURCE_USERNAME=root"
set "MYSQL_DATASOURCE_PASSWORD=root"
set "POSTGRES_DB=resume-builder"
set "POSTGRES_USER=pgvector"
set "POSTGRES_PASSWORD=pgvector"
set "PGVECTOR_PORT=5433"
set "PGVECTOR_DATASOURCE_USERNAME=pgvector"
set "PGVECTOR_DATASOURCE_PASSWORD=pgvector"
set "PGVECTOR_DATASOURCE_URL="
set "PYTHON_PGVECTOR_DATASOURCE_URL="
set "FLYWAY_MYSQL_URL="
set "FLYWAY_MYSQL_USER="
set "FLYWAY_MYSQL_PASSWORD="
set "FLYWAY_POSTGRES_URL="
set "FLYWAY_POSTGRES_USER="
set "FLYWAY_POSTGRES_PASSWORD="
set "NODE_IMAGE=node:22-alpine"
set "NGINX_IMAGE=nginx:alpine"
set "PYTHON_IMAGE=python:3.11-slim"
set "MYSQL_IMAGE=mysql:8.4"
set "PGVECTOR_IMAGE=pgvector/pgvector:pg17"
set "REDIS_IMAGE=redis:7-alpine"
set "MINIO_IMAGE=minio/minio:RELEASE.2024-12-18T13-15-44Z"
set "MINIO_MC_IMAGE=minio/mc:RELEASE.2024-11-21T17-21-54Z"
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
for /f "usebackq tokens=1,* delims==" %%A in ("%~1") do (
  if "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
  if "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
  if "%%A"=="BACKEND_PORT" set "BACKEND_PORT_SET=1"
  if "%%A"=="SERVER_PORT" set "SERVER_PORT=%%B"
  if "%%A"=="MYSQL_PORT" set "MYSQL_PORT=%%B"
  if "%%A"=="MYSQL_DATABASE" set "MYSQL_DATABASE=%%B"
  if "%%A"=="MYSQL_ROOT_PASSWORD" set "MYSQL_ROOT_PASSWORD=%%B"
  if "%%A"=="MYSQL_DATASOURCE_USERNAME" set "MYSQL_DATASOURCE_USERNAME=%%B"
  if "%%A"=="MYSQL_DATASOURCE_PASSWORD" set "MYSQL_DATASOURCE_PASSWORD=%%B"
  if "%%A"=="POSTGRES_DB" set "POSTGRES_DB=%%B"
  if "%%A"=="POSTGRES_USER" set "POSTGRES_USER=%%B"
  if "%%A"=="POSTGRES_PASSWORD" set "POSTGRES_PASSWORD=%%B"
  if "%%A"=="PGVECTOR_PORT" set "PGVECTOR_PORT=%%B"
  if "%%A"=="PGVECTOR_DATASOURCE_USERNAME" set "PGVECTOR_DATASOURCE_USERNAME=%%B"
  if "%%A"=="PGVECTOR_DATASOURCE_PASSWORD" set "PGVECTOR_DATASOURCE_PASSWORD=%%B"
  if "%%A"=="PGVECTOR_DATASOURCE_URL" set "PGVECTOR_DATASOURCE_URL=%%B"
  if "%%A"=="PYTHON_PGVECTOR_DATASOURCE_URL" set "PYTHON_PGVECTOR_DATASOURCE_URL=%%B"
  if "%%A"=="FLYWAY_MYSQL_URL" set "FLYWAY_MYSQL_URL=%%B"
  if "%%A"=="FLYWAY_MYSQL_USER" set "FLYWAY_MYSQL_USER=%%B"
  if "%%A"=="FLYWAY_MYSQL_PASSWORD" set "FLYWAY_MYSQL_PASSWORD=%%B"
  if "%%A"=="FLYWAY_POSTGRES_URL" set "FLYWAY_POSTGRES_URL=%%B"
  if "%%A"=="FLYWAY_POSTGRES_USER" set "FLYWAY_POSTGRES_USER=%%B"
  if "%%A"=="FLYWAY_POSTGRES_PASSWORD" set "FLYWAY_POSTGRES_PASSWORD=%%B"
  if "%%A"=="OPENAI_BASE_URL" set "OPENAI_BASE_URL=%%B"
  if "%%A"=="OPENAI_CHAT_BASE_URL" set "OPENAI_CHAT_BASE_URL=%%B"
  if "%%A"=="OPENAI_EMBEDDING_BASE_URL" set "OPENAI_EMBEDDING_BASE_URL=%%B"
  if "%%A"=="OPENAI_VISION_BASE_URL" set "OPENAI_VISION_BASE_URL=%%B"
  if "%%A"=="OPENAI_REALTIME_BASE_URL" set "OPENAI_REALTIME_BASE_URL=%%B"
  if "%%A"=="OLLAMA_EMBEDDING_BASE_URL" set "OLLAMA_EMBEDDING_BASE_URL=%%B"
  if "%%A"=="NODE_IMAGE" set "NODE_IMAGE=%%B"
  if "%%A"=="NGINX_IMAGE" set "NGINX_IMAGE=%%B"
  if "%%A"=="PYTHON_IMAGE" set "PYTHON_IMAGE=%%B"
  if "%%A"=="MYSQL_IMAGE" set "MYSQL_IMAGE=%%B"
  if "%%A"=="PGVECTOR_IMAGE" set "PGVECTOR_IMAGE=%%B"
  if "%%A"=="REDIS_IMAGE" set "REDIS_IMAGE=%%B"
  if "%%A"=="MINIO_IMAGE" set "MINIO_IMAGE=%%B"
  if "%%A"=="MINIO_MC_IMAGE" set "MINIO_MC_IMAGE=%%B"
)
if "%BACKEND_PORT_SET%"=="0" set "BACKEND_PORT=%SERVER_PORT%"
exit /b 0

:normalize_host_urls
call :normalize_one_url OPENAI_BASE_URL
call :normalize_one_url OPENAI_CHAT_BASE_URL
call :normalize_one_url OPENAI_EMBEDDING_BASE_URL
call :normalize_one_url OPENAI_VISION_BASE_URL
call :normalize_one_url OPENAI_REALTIME_BASE_URL
call :normalize_one_url OLLAMA_EMBEDDING_BASE_URL
call :normalize_one_url PGVECTOR_DATASOURCE_URL
call :normalize_one_url PYTHON_PGVECTOR_DATASOURCE_URL
call :normalize_one_url FLYWAY_MYSQL_URL
call :normalize_one_url FLYWAY_POSTGRES_URL
exit /b 0

:normalize_one_url
call set "CURRENT_VALUE=%%%~1%%"
if "%CURRENT_VALUE%"=="" exit /b 0
set "NORMALIZED_VALUE=%CURRENT_VALUE:localhost=host.docker.internal%"
set "NORMALIZED_VALUE=%NORMALIZED_VALUE:127.0.0.1=host.docker.internal%"
if not "%NORMALIZED_VALUE%"=="%CURRENT_VALUE%" (
  set "%~1=%NORMALIZED_VALUE%"
  echo [INFO] Rewrote %~1 for Docker host access.
)
exit /b 0

:is_external_pgvector_url
call set "CURRENT_VALUE=%%%~1%%"
if "%CURRENT_VALUE%"=="" exit /b 1
call :is_compose_pgvector_url %~1
if not errorlevel 1 exit /b 1
exit /b 0

:is_compose_pgvector_url
call set "CURRENT_VALUE=%%%~1%%"
if "%CURRENT_VALUE%"=="" exit /b 1
if not "%CURRENT_VALUE://pgvector:=%"=="%CURRENT_VALUE%" exit /b 0
if not "%CURRENT_VALUE:@pgvector:=%"=="%CURRENT_VALUE%" exit /b 0
exit /b 1

:is_compose_mysql_url
call set "CURRENT_VALUE=%%%~1%%"
if "%CURRENT_VALUE%"=="" exit /b 1
if not "%CURRENT_VALUE://mysql:=%"=="%CURRENT_VALUE%" exit /b 0
if not "%CURRENT_VALUE:@mysql:=%"=="%CURRENT_VALUE%" exit /b 0
exit /b 1

:validate_python_external_postgres_migration
call :is_compose_pgvector_url FLYWAY_POSTGRES_URL
if not errorlevel 1 set "FLYWAY_POSTGRES_URL="
if "%FLYWAY_POSTGRES_URL%"=="" (
  echo([ERROR] 外部 PostgreSQL 必须在环境文件中显式配置 FLYWAY_POSTGRES_URL。
  exit /b 1
)
exit /b 0

:remove_skipped_service
docker compose %COMPOSE_ENV% --profile python-ai rm -sf %~1 >nul 2>nul
exit /b 0

:disable_pgvector_service
set "PGVECTOR_PROFILE_PYTHON_AI=host-pgvector-disabled"
call :remove_skipped_service pgvector
exit /b 0

:wait_for_started_databases
for %%S in (%DB_SERVICES%) do (
  if "%%S"=="mysql" call :wait_for_healthy resume-builder-mysql 120
  if "%%S"=="pgvector" call :wait_for_healthy resume-builder-pgvector 120
  if errorlevel 1 exit /b 1
)
exit /b 0

:run_database_migrations
if not exist "sql\migrations\mysql" (
  echo([ERROR] 缺少 sql\migrations\mysql 迁移目录。
  exit /b 1
)
if not exist "sql\migrations\postgresql" (
  echo([ERROR] 缺少 sql\migrations\postgresql 迁移目录。
  exit /b 1
)
echo([INFO] 正在构建固定版本的 Flyway 运行镜像。
docker compose %COMPOSE_ENV% --profile migration build flyway-mysql
if errorlevel 1 exit /b 1
echo([INFO] 正在执行 MySQL 版本迁移。
docker compose %COMPOSE_ENV% --profile migration run --rm --no-deps flyway-mysql
if errorlevel 1 exit /b 1
echo([INFO] 正在执行 PostgreSQL 版本迁移。
docker compose %COMPOSE_ENV% --profile migration run --rm --no-deps flyway-pgvector
if errorlevel 1 exit /b 1
echo([INFO] 数据库版本迁移完成。
exit /b 0

:wait_for_healthy
set "WAIT_CONTAINER=%~1"
set "WAIT_LIMIT=%~2"
set "WAIT_COUNT=0"
:wait_for_healthy_loop
set "WAIT_STATUS="
for /f "delims=" %%H in ('docker inspect -f "{{.State.Health.Status}}" "%WAIT_CONTAINER%" 2^>nul') do set "WAIT_STATUS=%%H"
if "%WAIT_STATUS%"=="healthy" exit /b 0
if %WAIT_COUNT% GEQ %WAIT_LIMIT% (
  echo [ERROR] Container %WAIT_CONTAINER% did not become healthy.
  exit /b 1
)
set /a WAIT_COUNT+=1 >nul
timeout /t 1 /nobreak >nul
goto wait_for_healthy_loop

:wait_for_completed
set "WAIT_CONTAINER=%~1"
set "WAIT_LIMIT=%~2"
set "WAIT_COUNT=0"
:wait_for_completed_loop
set "WAIT_STATUS="
set "WAIT_EXIT_CODE="
for /f "delims=" %%S in ('docker inspect -f "{{.State.Status}}" "%WAIT_CONTAINER%" 2^>nul') do set "WAIT_STATUS=%%S"
for /f "delims=" %%E in ('docker inspect -f "{{.State.ExitCode}}" "%WAIT_CONTAINER%" 2^>nul') do set "WAIT_EXIT_CODE=%%E"
if "%WAIT_STATUS%"=="exited" if "%WAIT_EXIT_CODE%"=="0" exit /b 0
if "%WAIT_STATUS%"=="exited" (
  echo [ERROR] Container %WAIT_CONTAINER% exited with code %WAIT_EXIT_CODE%.
  exit /b 1
)
if %WAIT_COUNT% GEQ %WAIT_LIMIT% (
  echo [ERROR] Container %WAIT_CONTAINER% did not complete.
  exit /b 1
)
set /a WAIT_COUNT+=1 >nul
timeout /t 1 /nobreak >nul
goto wait_for_completed_loop

:wait_for_http
set "WAIT_URL=%~1"
set "WAIT_LIMIT=%~2"
set "WAIT_COUNT=0"
:wait_for_http_loop
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri '%WAIT_URL%' -TimeoutSec 3; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 exit /b 0
if %WAIT_COUNT% GEQ %WAIT_LIMIT% (
  echo [ERROR] HTTP service %WAIT_URL% did not become available.
  exit /b 1
)
set /a WAIT_COUNT+=1 >nul
timeout /t 1 /nobreak >nul
goto wait_for_http_loop

:ensure_build_images
set "MISSING_IMAGES=0"
call :ensure_image PYTHON_IMAGE
call :ensure_image MINIO_IMAGE
call :ensure_image MINIO_MC_IMAGE
if not "%MISSING_IMAGES%"=="0" exit /b 1
exit /b 0

:ensure_database_images
set "MISSING_IMAGES=0"
for %%S in (%DB_SERVICES%) do (
  if "%%S"=="mysql" call :ensure_image MYSQL_IMAGE
  if "%%S"=="pgvector" call :ensure_image PGVECTOR_IMAGE
)
if not "%MISSING_IMAGES%"=="0" exit /b 1
exit /b 0

:ensure_image
call set "IMAGE_VALUE=%%%~1%%"
if "%IMAGE_VALUE%"=="" exit /b 0
docker image inspect "%IMAGE_VALUE%" >nul 2>nul
if not errorlevel 1 exit /b 0
echo [INFO] Pulling Docker image for %~1: %IMAGE_VALUE%
docker pull "%IMAGE_VALUE%"
if errorlevel 1 (
  echo.
  echo [ERROR] Cannot pull required Docker image: %IMAGE_VALUE%
  echo [ERROR] If Docker Hub is unreachable, set %~1 in .env to a reachable registry mirror image.
  echo [ERROR] You can also docker pull or docker load this image manually, then rerun this script.
  set "MISSING_IMAGES=1"
)
exit /b 0

:is_port_listening
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue) { exit 0 }; exit 1"
exit /b %errorlevel%
