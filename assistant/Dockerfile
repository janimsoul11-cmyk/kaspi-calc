FROM python:3.13-slim

# ffmpeg нужен, чтобы голосовые ответы приходили как настоящие голосовые (OGG/Opus).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

ENV DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot"]
