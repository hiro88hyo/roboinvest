FROM python:3.12-slim

ARG SERVICE_PATH
ARG PACKAGE_NAME
ARG MODULE_NAME

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    PACKAGE_NAME=${PACKAGE_NAME} \
    MODULE_NAME=${MODULE_NAME}

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY contracts/python /app/contracts/python
COPY ${SERVICE_PATH} /app/${SERVICE_PATH}
COPY pyproject.toml uv.lock /app/

RUN uv sync --frozen --package "${PACKAGE_NAME}" --no-dev

ENTRYPOINT ["sh", "-c", "exec .venv/bin/python -m \"$MODULE_NAME\" \"$@\"", "--"]
