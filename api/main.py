import os
from fastapi import FastAPI, Query
from fastapi.responses import Response
from paramiko import SSHClient, AutoAddPolicy
from io import BytesIO
from PIL import Image
import mimetypes
from urllib.parse import unquote, quote  # Импорт для корректной работы с кириллицей в URL

# --- КОНФИГУРАЦИЯ СЕРВЕРА ---
# Директория с фотографиями на удаленном сервере
REMOTE_PHOTO_DIR = "/var/www/html/extencion_photo/"

# Базовый URL для прямых ссылок (для копирования)
BASE_URL = "https://tropicbridge.site/extencion_photo/"

# Размеры миниатюр
PREVIEW_SIZE = (200, 200)
# --- КОНФИГУРАЦИЯ СЕРВЕРА ---

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
SSH_HOST = os.environ.get("SSH_HOST")
SSH_USER = os.environ.get("SSH_USER")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD")

# Настройки пагинации
PAGE_SIZE = 20

app = FastAPI(
    title="Photo Manager API",
    description="API для доступа к файлам по SFTP.",
    version="1.0.0"
)


# --- SSH/SFTP ---

def get_sftp_client():
    """Подключается к удаленному серверу по SFTP."""
    if not all([SSH_HOST, SSH_USER, SSH_PASSWORD]):
        # Ошибка, если учетные данные SSH не установлены
        raise Exception("SSH credentials are not set in environment variables.")

    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    client.connect(hostname=SSH_HOST, username=SSH_USER, password=SSH_PASSWORD)
    sftp = client.open_sftp()
    return client, sftp


# --- ЭНДПОИНТЫ ---

@app.get("/api/list", response_model=dict)
async def list_files(page: int = Query(1, ge=1), query: str = Query("")):
    client, sftp = None, None
    try:
        client, sftp = get_sftp_client()

        # Получаем полный список файлов (игнорируем папки)
        file_list = [
            f.filename for f in sftp.listdir_attr(REMOTE_PHOTO_DIR)
            if f.st_mode is not None and not (f.st_mode & 0o40000)
        ]

        # Фильтрация по запросу
        if query:
            query_lower = query.lower()
            file_list = [f for f in file_list if query_lower in f.lower()]

        file_list.sort(key=lambda f: f.lower())

        total_files = len(file_list)
        total_pages = (total_files + PAGE_SIZE - 1) // PAGE_SIZE

        # Пагинация
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        paged_list = file_list[start:end]

        files_data = []
        for filename in paged_list:
            # Используем quote для URL-кодирования имени файла, чтобы избежать ошибок
            encoded_name = quote(filename, safe='')

            files_data.append({
                "name": filename,
                "https_url": f"{BASE_URL}{encoded_name}",
                "preview_url": f"/api/preview/{encoded_name}"
            })

        return {
            "files": files_data,
            "total_files": total_files,
            "total_pages": total_pages,
            "current_page": page
        }

    except Exception as e:
        print(f"Error listing files: {e}")
        return {"detail": str(e)}
    finally:
        if sftp:
            sftp.close()
        if client:
            client.close()


@app.get("/api/preview/{filename:path}")
async def get_photo_preview(filename: str, download: bool = Query(False)):
    client, sftp = None, None
    try:
        # 1. Корректное декодирование имени файла
        # Используем unquote для обратного URL-декодирования.
        decoded_filename = unquote(filename)

        # 2. Подключение и чтение
        client, sftp = get_sftp_client()
        remote_path = os.path.join(REMOTE_PHOTO_DIR, decoded_filename)

        file_buffer = BytesIO()
        sftp.getfo(remote_path, file_buffer)
        file_buffer.seek(0)

        # 3. Обработка изображения (миниатюра или полный размер)
        content = file_buffer.getvalue()
        media_type, _ = mimetypes.guess_type(decoded_filename)

        if not download and media_type and media_type.startswith('image/'):
            # Создание миниатюры только для предпросмотра
            try:
                img = Image.open(file_buffer)
                img.thumbnail(PREVIEW_SIZE)

                output = BytesIO()
                # Сохраняем в том же формате, что и исходный файл (если возможно)
                img.save(output, format=img.format if img.format else 'PNG')
                content = output.getvalue()
            except Exception as thumbnail_error:
                print(f"Error creating thumbnail for {decoded_filename}: {thumbnail_error}")
                # Если миниатюра не создана, возвращаем полный файл
                file_buffer.seek(0)
                content = file_buffer.getvalue()

        # 4. Установка заголовков
        headers = None
        if download:
            # ИСПРАВЛЕНИЕ 'latin-1' ОШИБКИ:
            # Используем RFC 5987 (filename*) для правильного кодирования кириллицы в заголовке Content-Disposition.
            # quote(decoded_filename, safe='') гарантирует, что имя файла будет корректно URL-закодировано для заголовка.
            encoded_header_filename = quote(decoded_filename, safe='')

            headers = {
                "Content-Disposition": f"attachment; filename=\"{decoded_filename}\"; filename*=utf-8''{encoded_header_filename}"
            }

        # Если тип MIME не определен, используем 'application/octet-stream' для бинарных данных
        if not media_type:
            media_type = 'application/octet-stream'

        return Response(content=content, media_type=media_type, headers=headers)

    except FileNotFoundError:
        # Возвращаем 404
        return Response(status_code=404, content='{"detail": "File not found"}', media_type="application/json")
    except Exception as e:
        print(f"Error retrieving file: {e}")
        # Возвращаем 500
        return Response(status_code=500, content='{"detail": "Internal Server Error"}', media_type="application/json")
    finally:
        if sftp:
            sftp.close()
        if client:
            client.close()