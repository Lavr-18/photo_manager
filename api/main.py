import os
import re
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import Response
from paramiko import SSHClient, AutoAddPolicy
from io import BytesIO
from PIL import Image
import mimetypes
from urllib.parse import unquote, quote

# --- КОНФИГУРАЦИЯ СЕРВЕРА ---
# Директория с фотографиями на удаленном сервере
REMOTE_PHOTO_DIR = "/var/www/html/extencion_photo/"

# Базовый URL для прямых ссылок (для копирования)
# ИЗМЕНЕНО: Добавлен порт 8443
BASE_URL = "https://tropicbridge.site:8443/extencion_photo/"

# Размеры миниатюр
PREVIEW_SIZE = (200, 200)

# --- КОНФИГУРАЦИЯ MOYSKLAD ---
MOYSKLAD_API_URL = "https://api.moysklad.ru/api/remap/1.2/entity/product"

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
SSH_HOST = os.environ.get("SSH_HOST")
SSH_USER = os.environ.get("SSH_USER")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD")
MOYSKLAD_API_TOKEN = os.environ.get("MOYSKLAD_API_TOKEN")  # Токен для МойСклад

# Настройки пагинации
PAGE_SIZE = 20

app = FastAPI(
    title="Photo Manager API",
    description="API для доступа к файлам по SFTP и ценам МойСклад.",
    version="1.0.0"
)


# --- SSH/SFTP ---

def get_sftp_client():
    """Подключается к удаленному серверу по SFTP."""
    if not all([SSH_HOST, SSH_USER, SSH_PASSWORD]):
        raise Exception("SSH credentials are not set in environment variables.")

    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    client.connect(hostname=SSH_HOST, username=SSH_USER, password=SSH_PASSWORD)
    sftp = client.open_sftp()
    return client, sftp


# --- ФУНКЦИИ ОБРАБОТКИ ИЗОБРАЖЕНИЙ ---

def rotate_by_exif(img: Image) -> Image:
    """
    Автоматически поворачивает изображение на основе его EXIF-данных (тег 274: Orientation).
    """
    try:
        # Тег 274: Orientation
        exif = img._getexif()
        if exif is None:
            return img

        orientation = exif.get(0x0112)

        # Применяем преобразования в зависимости от значения ориентации
        if orientation == 2:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            img = img.transpose(Image.ROTATE_180)
        elif orientation == 4:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            img = img.transpose(Image.TRANSPOSE)  # 90 degrees counter-clockwise and flip vertically
        elif orientation == 6:
            img = img.transpose(Image.ROTATE_270)  # 270 degrees clockwise
        elif orientation == 7:
            img = img.transpose(Image.TRANSVERSE)  # 90 degrees clockwise and flip vertically
        elif orientation == 8:
            img = img.transpose(Image.ROTATE_90)  # 90 degrees clockwise

        # Очищаем EXIF-данные, чтобы избежать двойного поворота в браузере
        if orientation:
            img.info['exif'] = None

    except Exception as e:
        # Игнорируем ошибки, если EXIF-данные повреждены или отсутствуют
        print(f"Error during EXIF rotation: {e}")

    return img


# --- ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕНЫ ИЗ МОЙСКЛАД ---

async def get_price_from_moysklad(product_name: str) -> str:
    """Получает цену товара из МойСклад по его названию."""
    if not MOYSKLAD_API_TOKEN:
        print("Warning: MOYSKLAD_API_TOKEN not set. Returning 'Нет данных'")
        return "Нет данных"

    # 1. ИСПРАВЛЕНИЕ: Вручную URL-кодируем название товара, используя quote.
    encoded_product_name = quote(product_name)

    # 2. Формируем строку запроса filter=name=...
    query_string = f"filter=name={encoded_product_name}"

    headers = {
        "Authorization": f"Bearer {MOYSKLAD_API_TOKEN}",
        "Accept-Encoding": "gzip"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 3. ИЗМЕНЕНИЕ: Используем URL с закодированной строкой, а не словарь params
            response = await client.get(
                f"{MOYSKLAD_API_URL}?{query_string}",
                headers=headers
            )
            response.raise_for_status()

        data = response.json()

        # 2. Извлечение цены из ответа (rows[0].salePrices[0].value)
        if data.get('rows') and len(data['rows']) > 0:
            row = data['rows'][0]
            if row.get('salePrices') and len(row['salePrices']) > 0:
                price_value = row['salePrices'][0].get('value')

                # Цена в МойСклад хранится в копейках. Делим на 100 и форматируем.
                if price_value is not None:
                    price_rub = round(price_value / 100)
                    return f"{price_rub:,} ₽".replace(",", " ")

        return "Нет цены"

    except httpx.HTTPStatusError as e:
        print(f"Moysklad API Error ({e.response.status_code}): {e}")
        return "Ошибка API"
    except Exception as e:
        print(f"Error fetching Moysklad price for '{product_name}': {e}")
        return "Ошибка"


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
            # Используем quote для URL-кодирования имени файла
            encoded_name = quote(filename, safe='')

            # 1. Извлекаем название товара: имя файла без расширения
            # ИСПРАВЛЕНИЕ: Добавлен .strip() для удаления лишних пробелов
            product_name = os.path.splitext(filename)[0].strip()

            # 2. ИСПРАВЛЕНИЕ: Заменяем символ '_' на '/' для точного поиска в МойСклад
            moysklad_name = product_name.replace('_', '/')

            # 3. Асинхронно получаем цену
            price = await get_price_from_moysklad(moysklad_name)

            files_data.append({
                "name": filename,
                "https_url": f"{BASE_URL}{encoded_name}",
                "preview_url": f"/api/preview/{encoded_name}",
                "price": price
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

                # НОВОЕ ИСПРАВЛЕНИЕ: Коррекция ориентации на основе EXIF-данных
                img = rotate_by_exif(img)

                img.thumbnail(PREVIEW_SIZE)

                output = BytesIO()
                img.save(output, format=img.format if img.format else 'PNG')
                content = output.getvalue()
            except Exception as thumbnail_error:
                print(f"Error creating thumbnail for {decoded_filename}: {thumbnail_error}")
                file_buffer.seek(0)
                content = file_buffer.getvalue()

        # 4. Установка заголовков
        headers = None
        if download:
            encoded_header_filename = quote(decoded_filename)
            ascii_fallback_filename = decoded_filename.encode('ascii', 'ignore').decode('ascii')
            if not ascii_fallback_filename:
                ascii_fallback_filename = "download"

            headers = {
                "Content-Disposition": f"attachment; filename=\"{ascii_fallback_filename}\"; filename*=utf-8''{encoded_header_filename}"
            }

        if not media_type:
            media_type = 'application/octet-stream'

        return Response(content=content, media_type=media_type, headers=headers)

    except FileNotFoundError:
        return Response(status_code=404, content='{"detail": "File not found"}', media_type="application/json")
    except Exception as e:
        print(f"Error retrieving file: {e}")
        return Response(status_code=500, content='{"detail": "Internal Server Error"}', media_type="application/json")
    finally:
        if sftp:
            sftp.close()
        if client:
            client.close()