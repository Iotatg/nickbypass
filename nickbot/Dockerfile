# Build from the repo root:
#   docker build -t nickbot .
#   docker run -d --name nickbot --env-file nickbot/.env -v nickdata:/app/nickbot/data nickbot
#
# Browser fallback is off by default (image is ~280 MB). Build with
#   docker build --build-arg WITH_BROWSER=1 -t nickbot:browser .
# if you want Chromium inside the container (well over 1 GB).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/nickbot

COPY nickbot/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

ARG WITH_BROWSER=0
RUN if [ "$WITH_BROWSER" = "1" ]; then \
        pip install playwright && playwright install --with-deps chromium; \
    else \
        pip uninstall -y playwright >/dev/null 2>&1 || true; \
    fi

# the bot imports the resolver engine; keep it next to the package and say so
COPY nickbot/ ./
COPY linkresolver/ ./linkresolver/
ENV LINKRESOLVER_HOME=/app/nickbot/linkresolver

# defaults only — anything in your --env-file or a mounted .env overrides these
RUN printf 'ALLOW_BROWSER=%s\nPROXY_FARM=%s\n' \
      "$( [ "$WITH_BROWSER" = "1" ] && echo true || echo false )" \
      "false" > .env

RUN mkdir -p data
VOLUME ["/app/nickbot/data"]

HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD python -m nickbot --check >/dev/null || exit 1

CMD ["python", "-m", "nickbot"]
