import os
import re  # Новый импорт для работы с именем файла
import httpx  # Новый импорт для HTTP-запросов
from fastapi import FastAPI, Query
from fastapi.responses import Response
from paramiko import SSHClient, AutoAddPolicy
from io import BytesIO
from PIL import Image
import mimetypes
# import random # Удаляем random, так как он больше не нужен
from urllib.parse import unquote, quote

# --- КОНФИГУРАЦИЯ СЕРВЕРА ---
REMOTE_PHOTO_DIR = "/var/www/html/extencion_photo/"
BASE_URL = "https://tropicbridge.site/extencion_photo/"
PREVIEW_SIZE = (200, 200)

# --- КОНФИГУРАЦИЯ MOYSKLAD ---
MOYSKLAD_API_URL = "https://api.moysklad.ru/api/remap/1.2/entity/product"
# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
SSH_HOST = os.environ.get("SSH_HOST")
SSH_USER = os.environ.get("SSH_USER")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD")
MOYSKLAD_API_TOKEN = os.environ.get("MOYSKLAD_API_TOKEN")  # Новый токен

# Настройки пагинации
PAGE_SIZE = 20

app = FastAPI(
    title="Photo Manager API",
    description="API для доступа к файлам по SFTP и ценам МойСклад.",
    version="1.0.0"
)


# --- SSH/SFTP (Без изменений) ---

def get_sftp_client():
    """Подключается к удаленному серверу по SFTP."""
    if not all([SSH_HOST, SSH_USER, SSH_PASSWORD]):
        raise Exception("SSH credentials are not set in environment variables.")

    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    client.connect(hostname=SSH_HOST, username=SSH_USER, password=SSH_PASSWORD)
    sftp = client.open_sftp()
    return client, sftp


# --- ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕНЫ ИЗ МОЙСКЛАД ---

async def get_price_from_moysklad(product_name: str) -> str:
    """Получает цену товара из МойСклад по его названию."""
    if not MOYSKLAD_API_TOKEN:
        print("Warning: MOYSKLAD_API_TOKEN not set. Returning 'Нет данных'")
        return "Нет данных"

    # 1. Формирование URL-запроса с фильтром по названию
    params = {
        "filter": f"name={product_name}"
    }
    headers = {
        "Authorization": f"Bearer {MOYSKLAD_API_TOKEN}",
        "Accept-Encoding": "gzip"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                MOYSKLAD_API_URL,
                params=params,
                headers=headers
            )
            response.raise_for_status()  # Вызывает исключение для 4xx/5xx ответов

        data = response.json()

        # 2. Извлечение цены из ответа (rows[0].salePrices[0].value)
        if data.get('rows') and len(data['rows']) > 0:
            row = data['rows'][0]
            if row.get('salePrices') and len(row['salePrices']) > 0:
                price_value = row['salePrices'][0].get('value')

                # Цена в МойСклад хранится в копейках. Делим на 100 и форматируем.
                if price_value is not None:
                    # Форматирование: деление на 100, округление, добавление разделителя и знака валюты.
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

        # ... (Код получения и фильтрации file_list без изменений)

        file_list = [
            f.filename for f in sftp.listdir_attr(REMOTE_PHOTO_DIR)
            if f.st_mode is not None and not (f.st_mode & 0o40000)
        ]

        if query:
            query_lower = query.lower()
            file_list = [f for f in file_list if query_lower in f.lower()]

        file_list.sort(key=lambda f: f.lower())

        total_files = len(file_list)
        total_pages = (total_files + PAGE_SIZE - 1) // PAGE_SIZE

        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        paged_list = file_list[start:end]

        files_data = []
        for filename in paged_list:
            encoded_name = quote(filename, safe='')

            # --- ЛОГИКА ИЗВЛЕЧЕНИЯ НАЗВАНИЯ И ПОЛУЧЕНИЯ ЦЕНЫ ---

            # Извлекаем название товара: имя файла без расширения
            # 'Фикус.png' -> 'Фикус'
            # 'Название.товара.jpg' -> 'Название.товара'
            product_name = os.path.splitext(filename)[0]

            # Асинхронно получаем цену
            price = await get_price_from_moysklad(product_name)

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
# ... (Код get_photo_preview остается без изменений)
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
