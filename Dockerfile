# StudyHub in a container. Quick start (see README → "Run it with Docker"):
#   docker compose run --rm studyhub init     # set the owner password (personal site)
#   docker compose up -d                      # http://127.0.0.1:8091
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    STUDY_CONFIG_FILE=/data/config.json STUDY_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt /app/studyhub/requirements.txt
RUN pip install --no-cache-dir -r /app/studyhub/requirements.txt \
 && useradd --system --uid 10001 --home /data studyhub \
 && mkdir -p /data && chown studyhub /data
# The repository folder is the Python package, so it lives at /app/studyhub.
COPY --chown=root:root . /app/studyhub
USER studyhub
VOLUME /data
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health')"
ENTRYPOINT ["python", "-m", "studyhub"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8091"]
