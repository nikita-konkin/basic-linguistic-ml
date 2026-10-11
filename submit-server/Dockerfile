# Приёмник сданных работ: только стандартная библиотека Python.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8095 \
    DATA_DIR=/data

WORKDIR /app
COPY server.py .

# Пользователь задаётся в docker-compose (user: "1000:1000"), чтобы файлы в
# data/ на сервере принадлежали владельцу папки; по умолчанию — не root.
USER nobody
EXPOSE 8095

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/health', timeout=4)"

CMD ["python", "server.py"]
