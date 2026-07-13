FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd -u 10002 -m webui

COPY web/requirements.txt .
RUN pip install -r requirements.txt

COPY web/app ./app

RUN mkdir -p /data && chown -R webui:webui /data /app
USER webui

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/login').status in (200,302,307) else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
