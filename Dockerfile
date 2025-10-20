# =======================================================
# Dockerfile
# =======================================================
FROM python:3.10-slim

# Установка системных зависимостей для paramiko и Pillow
# Это должно быть выполнено на этапе сборки образа
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем только файлы, необходимые для API, из папки api/
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем сам код API
# Обратите внимание: main.py теперь находится в корне /app
COPY api/main.py .

# Указываем, какой порт слушает контейнер (внутри)
EXPOSE 8000

# Запуск приложения с Gunicorn и Uvicorn workers
# Gunicorn обеспечивает стабильность в продакшене.
CMD ["gunicorn", "main:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]