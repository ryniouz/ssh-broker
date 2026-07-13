FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# non-root user; broker holds the key so we minimise its blast radius
RUN useradd -u 10001 -m broker

COPY api/requirements.txt .
RUN pip install -r requirements.txt

COPY api/app ./app
COPY api/plugins ./plugins

RUN mkdir -p /data && chown -R broker:broker /data /app
USER broker

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
