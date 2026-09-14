ARG PYTHON_IMAGE=python:3.11-slim

# Python 后端镜像默认安装核心依赖和可选 AI 依赖，保证容器模式下 Chat、RAG、Realtime 能力完整。
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SERVER_PORT=8999

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt requirements-optional.txt ./

ARG INSTALL_OPTIONAL_AI_DEPS=true
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL_AI_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

COPY app ./app

EXPOSE 8999

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('SERVER_PORT', '8999'), timeout=3)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${SERVER_PORT:-8999}"]
