FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

# ワークスペース参照のため contracts/ と services/universe-scanner/ を同構造でコピー
COPY contracts/python /app/contracts/python
COPY services/universe-scanner /app/services/universe-scanner
COPY pyproject.toml uv.lock /app/

RUN uv sync --frozen --package universe-scanner --no-dev

ENTRYPOINT ["uv", "run", "--package", "universe-scanner", "python", "-m", "universe_scanner"]
