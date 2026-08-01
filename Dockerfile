# InstagramAgent — FastAPI uygulama imajı
FROM python:3.12-slim

# Log'ların anlık akması ve .pyc üretilmemesi için
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Bağımlılıklar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kaynağı
COPY . .

# Başlangıç betiği: migration + uygulama
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# Konteyner: önce şema migration'ı, sonra uvicorn (bkz. docker-entrypoint.sh)
ENTRYPOINT ["./docker-entrypoint.sh"]
