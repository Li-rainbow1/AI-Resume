@echo off
setlocal

REM 只停止并移除当前 Docker Compose stack，不删除 MySQL 和 pgvector 数据卷。
docker compose down
exit /b %errorlevel%
