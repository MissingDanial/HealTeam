# Build context: the healteam source root.
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.lock ./
RUN pip install --prefix=/install -r requirements.lock
COPY . ./
RUN pip install --prefix=/install --no-deps .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEALTEAM_ROOT=/opt/healteam \
    HEALTEAM_MODEL_MODE=mock
RUN groupadd --gid 1001 healteam && useradd --uid 1001 --gid healteam --create-home healteam
WORKDIR /opt/healteam
COPY --from=builder /install /usr/local
COPY --from=builder --chown=healteam:healteam /build/apps/api ./apps/api
COPY --from=builder --chown=healteam:healteam /build/packages ./packages
COPY --from=builder --chown=healteam:healteam /build/data ./data
COPY --from=builder --chown=healteam:healteam /build/apps/web/public ./apps/web/public
COPY --from=builder --chown=healteam:healteam /build/repo_bootstrap.py ./
RUN mkdir -p /var/lib/healteam && chown healteam:healteam /var/lib/healteam
USER healteam
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
