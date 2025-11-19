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
REMOTE_PHOTO_DIR = "/var/www/html/extencion_photo/"
BASE_URL = "https://tropicbridge.site:8443/extencion_photo/"
PREVIEW_SIZE = (200, 200)

# --- КОНФИГУРАЦИЯ MOYSKLAD ---
MOYSKLAD_API_URL = "https://api.moysklad.ru/api/remap/1.2/entity/product"
MOYSKLAD_STOCK_URL = "https://api.moysklad.ru/api/remap/1.2/report/stock/all"
STOCK_LIMIT = 1000  # Лимит пагинации для отчета по остаткам

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
SSH_HOST = os.environ.get("SSH_HOST")
SSH_USER = os.environ.get("SSH_USER")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD")
MOYSKLAD_API_TOKEN = os.environ.get("MOYSKLAD_API_TOKEN")

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
        exif = img._getexif()
        if exif is None:
            return img

        orientation = exif.get(0x0112)

        if orientation == 2:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            img = img.transpose(Image.ROTATE_180)
        elif orientation == 4:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            img = img.transpose(Image.TRANSPOSE)
        elif orientation == 6:
            img = img.transpose(Image.ROTATE_270)
        elif orientation == 7:
            img = img.transpose(Image.TRANSVERSE)
        elif orientation == 8:
            img = img.transpose(Image.ROTATE_90)

        # Очищаем EXIF-данные
        if orientation:
            img.info['exif'] = None

    except Exception as e:
        print(f"Error during EXIF rotation: {e}")

    return img


# --- ФУНКЦИИ МОЙСКЛАД ---

async def get_price_from_moysklad(product_name: str) -> str:
    """Получает цену товара из МойСклад по его названию."""
    if not MOYSKLAD_API_TOKEN:
        print("Warning: MOYSKLAD_API_TOKEN not set. Returning 'Нет данных'")
        return "Нет данных"

    # Вручную URL-кодируем название товара
    encoded_product_name = quote(product_name)
    query_string = f"filter=name={encoded_product_name}"

    # ИСПРАВЛЕНИЕ: Добавлен корректный заголовок Accept
    headers = {
        "Authorization": f"Bearer {MOYSKLAD_API_TOKEN}",
        "Accept-Encoding": "gzip",
        "Accept": "application/json;charset=utf-8"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Используем URL с закодированной строкой
            response = await client.get(
                f"{MOYSKLAD_API_URL}?{query_string}",
                headers=headers
            )
            response.raise_for_status()

        data = response.json()

        if data.get('rows') and len(data['rows']) > 0:
            row = data['rows'][0]
            if row.get('salePrices') and len(row['salePrices']) > 0:
                price_value = row['salePrices'][0].get('value')

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


async def get_all_stock_data() -> dict[str, float]:
    """
    Получает полный отчет по остаткам с пагинацией и возвращает словарь
    {название_товара: stock}.
    """
    if not MOYSKLAD_API_TOKEN:
        print("Warning: MOYSKLAD_API_TOKEN not set for stock check.")
        return {}

    all_stock_rows = {}
    offset = 0
    total_size = float('inf')

    # ИСПРАВЛЕНИЕ: Добавлен корректный заголовок Accept
    headers = {
        "Authorization": f"Bearer {MOYSKLAD_API_TOKEN}",
        "Accept-Encoding": "gzip",
        "Accept": "application/json;charset=utf-8"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            while offset < total_size:
                params = {
                    "stockMode": "all",
                    "limit": STOCK_LIMIT,
                    "offset": offset
                }

                response = await client.get(
                    MOYSKLAD_STOCK_URL,
                    params=params,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()

                rows = data.get('rows', [])
                if not rows:
                    break

                if 'meta' in data and 'size' in data['meta']:
                    total_size = data['meta']['size']

                for row in rows:
                    name = row.get('name')
                    # Используем 'stock' (текущий остаток)
                    stock = row.get('stock', 0)
                    if name:
                        # Используем очищенное название как ключ
                        # name.strip() соответствует имени из МойСклад (с пробелами и /)
                        all_stock_rows[name.strip()] = stock

                offset += STOCK_LIMIT

        return all_stock_rows

    except httpx.HTTPStatusError as e:
        print(f"Moysklad Stock API Error ({e.response.status_code}): {e}")
        return {}
    except Exception as e:
        print(f"Error fetching Moysklad stock data: {e}")
        return {}


# --- ЭНДПОИНТЫ ---

@app.get("/api/list", response_model=dict)
async def list_files(
        page: int = Query(1, ge=1),
        query: str = Query(""),
        in_stock: bool = Query(False)  # НОВЫЙ ПАРАМЕТР: фильтр по наличию
):
    client, sftp = None, None
    try:
        client, sftp = get_sftp_client()

        # 1. Если включен фильтр "В наличии", получаем полный отчет по остаткам
        stock_data = {}
        if in_stock:
            print("Fetching all stock data for filtering...")
            stock_data = await get_all_stock_data()
            print(f"Stock data fetched. Total items: {len(stock_data)}")

        # Получаем полный список файлов (игнорируем папки)
        file_list = [
            f.filename for f in sftp.listdir_attr(REMOTE_PHOTO_DIR)
            if f.st_mode is not None and not (f.st_mode & 0o40000)
        ]

        # 2. Фильтрация по наличию (in_stock)
        if in_stock:
            filtered_list = []
            for filename in file_list:
                # Извлекаем название товара и очищаем его, как для API
                # Убираем расширение, чистим пробелы, меняем _ на /
                product_name_raw = os.path.splitext(filename)[0].strip()
                moysklad_name = product_name_raw.replace('_', '/')

                # Проверяем наличие: stock > 0
                stock_value = stock_data.get(moysklad_name, 0)
                if stock_value > 0:
                    filtered_list.append(filename)
            file_list = filtered_list

        # 3. Фильтрация по запросу (query)
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
            encoded_name = quote(filename, safe='')

            # Извлекаем название товара и чистим пробелы
            product_name = os.path.splitext(filename)[0].strip()
            moysklad_name = product_name.replace('_', '/')

            # Асинхронно получаем цену
            price = await get_price_from_moysklad(moysklad_name)

            # Получаем остаток (или 0, если stock_data не загружался/товара нет)
            # Этот же механизм используется для фильтрации, если in_stock=True
            stock_value = stock_data.get(moysklad_name, 0)

            files_data.append({
                "name": filename,
                "https_url": f"{BASE_URL}{encoded_name}",
                "preview_url": f"/api/preview/{encoded_name}",
                "price": price,
                "stock": stock_value  # Добавляем текущий остаток
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
        decoded_filename = unquote(filename)
        client, sftp = get_sftp_client()
        remote_path = os.path.join(REMOTE_PHOTO_DIR, decoded_filename)

        file_buffer = BytesIO()
        sftp.getfo(remote_path, file_buffer)
        file_buffer.seek(0)

        content = file_buffer.getvalue()
        media_type, _ = mimetypes.guess_type(decoded_filename)

        if not download and media_type and media_type.startswith('image/'):
            try:
                img = Image.open(file_buffer)

                # ИСПРАВЛЕНИЕ: Коррекция ориентации на основе EXIF-данных
                img = rotate_by_exif(img)

                img.thumbnail(PREVIEW_SIZE)

                output = BytesIO()
                # img.format должен быть установлен Pillow при открытии
                img.save(output, format=img.format if img.format else 'PNG')
                content = output.getvalue()
            except Exception as thumbnail_error:
                print(f"Error creating thumbnail for {decoded_filename}: {thumbnail_error}")
                file_buffer.seek(0)
                content = file_buffer.getvalue()

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
