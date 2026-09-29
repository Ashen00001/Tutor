FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JP_TUTOR_DATA=/data HOST=0.0.0.0 PORT=8000

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . \
 && useradd -u 10001 -m tutor \
 && mkdir -p /data && chown tutor /data

USER tutor
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["anki-jp-tutor"]
