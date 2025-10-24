from dotenv import load_dotenv
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
import paramiko
from io import BytesIO
from PIL import Image
from urllib.parse import quote, unquote
import urllib.parse

# Загружаем переменные окружения из файла .env (ТОЛЬКО ЛОКАЛЬНО!)
# В продакшене Docker передаст SSH_PASSWORD напрямую.
load_dotenv()

# --- Конфигурация SSH и API ---
# Сервер, к которому подключаемся по SSH (хранит фото)
SSH_HOST = os.getenv("SSH_HOST")
SSH_USER = os.getenv("SSH_USER")
# Пароль будет получен из .env (локально) или из Docker-контейнера (на сервере)
SSH_PASSWORD = os.getenv("SSH_PASSWORD")

# Директория с фотографиями на удаленном сервере
REMOTE_PHOTO_DIR = "/var/www/html/extencion_photo/"

# Базовый URL для прямых ссылок (для копирования)
BASE_URL = "https://tropicbridge.site/extencion_photo/"

# Размер миниатюр
PREVIEW_SIZE = (150, 150)
PAGE_SIZE = 20

app = FastAPI()

# --- Вспомогательная функция для SSH-подключения ---
def get_ssh_client():
    """Устанавливает и возвращает SSH-клиент, настроенный для подключения по паролю."""
    if not SSH_PASSWORD:
        raise HTTPException(status_code=500, detail="Ошибка конфигурации: Пароль SSH не установлен.")

    client = paramiko.SSHClient()
    # Автоматически добавлять ключ хоста (удобно для первого подключения)
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=SSH_HOST, username=SSH_USER, password=SSH_PASSWORD, timeout=10)
        return client
    except Exception as e:
        print(f"Ошибка SSH-подключения: {e}")
        # Возвращаем 503, если не можем подключиться к серверу с фото
        raise HTTPException(status_code=503, detail="Ошибка подключения к серверу с фотографиями.")

# --- Эндпоинт: Получение списка файлов с пагинацией и поиском ---
@app.get("/api/list")
def get_file_list(page: int = 1, query: str = ""):
    """Возвращает пагинированный и отфильтрованный список файлов."""
    client = None
    try:
        client = get_ssh_client()
        sftp = client.open_sftp()

        all_files = sftp.listdir(REMOTE_PHOTO_DIR)

        # Фильтрация (поиск) и исключение скрытых файлов
        filtered_files = [f for f in all_files if not f.startswith('.') and f.lower().find(query.lower()) != -1]
        filtered_files.sort()

        # Пагинация
        total_files = len(filtered_files)
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE

        paginated_files = filtered_files[start:end]

        file_list = []
        for filename in paginated_files:
            # URL-кодирование для корректной передачи пробелов/спецсимволов в ссылках
            encoded_filename = quote(filename)

            file_list.append({
                "name": filename,
                "https_url": f"{BASE_URL}{encoded_filename}",
                # Ссылка на миниатюру через наш API (по ней расширение запросит превью)
                "preview_url": app.url_path_for("get_preview", filename=encoded_filename)
            })

        return JSONResponse(content={
            "files": file_list,
            "total_files": total_files,
            "total_pages": (total_files + PAGE_SIZE - 1) // PAGE_SIZE,
            "current_page": page
        })

    finally:
        if client:
            client.close()

# --- Эндпоинт: Генерация и отдача миниатюр ---
@app.get("/api/preview/{filename}", response_class=StreamingResponse, name="get_preview")
def get_preview(filename: str):
    """Отдает сгенерированную миниатюру изображения."""
    client = None
    try:
        client = get_ssh_client()
        sftp = client.open_sftp()

        # Декодируем имя файла, которое пришло из URL
        decoded_filename = unquote(filename)

        remote_path = os.path.join(REMOTE_PHOTO_DIR, decoded_filename)

        # 1. Скачиваем файл в буфер в памяти
        file_buffer = BytesIO()
        sftp.getfo(remote_path, file_buffer)
        file_buffer.seek(0)

        # 2. Обработка изображения и генерация превью
        img = Image.open(file_buffer)
        img.thumbnail(PREVIEW_SIZE)

        # Сохраняем миниатюру в новый буфер
        output_buffer = BytesIO()
        img.save(output_buffer, format="JPEG")
        output_buffer.seek(0)

        # 3. Отдаем как стриминговый ответ
        return StreamingResponse(
            output_buffer,
            media_type="image/jpeg",
            # Кэшируем превью в браузере на 24 часа
            headers={"Cache-Control": "public, max-age=86400"}
        )

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Файл не найден на сервере.")
    except Exception as e:
        print(f"Ошибка при обработке превью: {e}")
        # Ловим ошибки, связанные с чтением изображений (например, если файл не JPEG)
        raise HTTPException(status_code=500, detail="Ошибка обработки изображения.")
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    # Код для удобного локального запуска
    import uvicorn
    # Локальный запуск на порту 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)