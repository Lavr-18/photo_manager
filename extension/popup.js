const API_BASE_URL = "http://37.220.81.157:8088";
let currentPage = 1;
let currentQuery = "";
let totalPages = 1;
// НОВОЕ: Переменная состояния для фильтра по наличию
let isStockFilterActive = false;

const grid = document.getElementById('photos-grid');
const searchInput = document.getElementById('search-input');
// НОВОЕ: Чекбокс
const stockCheckbox = document.getElementById('stock-filter-checkbox');
const prevBtn = document.getElementById('prev-page');
const nextBtn = document.getElementById('next-page');
const pageInfo = document.getElementById('page-info');
const messageElement = document.getElementById('message');

// --- 1. Основная функция загрузки данных ---
// Обновлена: принимает inStockFilter
async function loadPhotos(page, query, inStockFilter) {
    messageElement.textContent = 'Загрузка...';
    grid.innerHTML = '';

    // Формируем строку запроса с новым параметром
    const stockParam = inStockFilter ? '&in_stock=true' : '';
    const url = `${API_BASE_URL}/api/list?page=${page}&query=${query}${stockParam}`;

    try {
        const response = await fetch(url);

        if (!response.ok) {
            const errorBody = await response.json();
            throw new Error(errorBody.detail || `Ошибка API: ${response.status}`);
        }

        const data = await response.json();

        data.files.forEach(file => {
            const photoItem = createPhotoElement(file);
            grid.appendChild(photoItem);
        });

        // Обновление пагинации
        currentPage = data.current_page;
        totalPages = data.total_pages;
        pageInfo.textContent = `Страница ${currentPage} из ${totalPages}`;
        prevBtn.disabled = currentPage <= 1;
        nextBtn.disabled = currentPage >= totalPages;

        messageElement.textContent = data.files.length === 0 ? 'Фотографии не найдены.' : '';

    } catch (error) {
        console.error('Ошибка загрузки фото:', error);
        messageElement.textContent = `Ошибка: ${error.message}`;
        prevBtn.disabled = true;
        nextBtn.disabled = true;
    }
}

// --- 2. Создание элемента фотографии и кнопок ---
function createPhotoElement(file) {
    const item = document.createElement('div');
    // Добавляем класс 'in-stock' если товар в наличии (stock > 0)
    const isInStock = file.stock > 0;
    item.className = `photo-item ${isInStock ? 'in-stock' : 'out-of-stock'}`;

    // Изображение
    const img = document.createElement('img');
    img.src = `${API_BASE_URL}${file.preview_url}`;
    img.alt = file.name;
    item.appendChild(img);

    // Имя файла
    const name = document.createElement('p');
    name.className = 'item-name';
    name.textContent = file.name;
    name.title = file.name;
    item.appendChild(name);

    // Цена и Остаток (НОВОЕ: Отображение остатка)
    const priceStock = document.createElement('p');
    priceStock.className = 'item-price-stock';

    let stockText;
    if (file.stock === undefined) {
        // Если поле 'stock' не пришло (теоретически не должно быть после нашего фикса)
        stockText = 'Нет данных';
    } else if (file.stock > 0) {
        stockText = `В наличии: ${Math.round(file.stock)}`;
    } else {
        stockText = 'Нет в наличии';
    }

    // Форматирование: Цена | Остаток
    priceStock.textContent = `${file.price} | ${stockText}`;
    item.appendChild(priceStock);


    // Кнопки действий
    const actions = document.createElement('div');
    actions.className = 'actions';

    // Кнопка 1: Копировать цену
    const copyPriceBtn = document.createElement('button');
    copyPriceBtn.textContent = '₽ Цена';
    copyPriceBtn.className = 'small-btn';
    copyPriceBtn.onclick = () => copyTextToClipboard(file.price, 'Цена скопирована!');
    actions.appendChild(copyPriceBtn);

    // Кнопка 2: Копировать название и цену
    const copyNamePriceBtn = document.createElement('button');
    copyNamePriceBtn.textContent = '📝 Название + ₽';
    copyNamePriceBtn.className = 'small-btn';
    const namePriceText = `${file.display_name} - ${file.price}`;
    copyNamePriceBtn.onclick = () => copyTextToClipboard(namePriceText, 'Название и цена скопированы!');
    actions.appendChild(copyNamePriceBtn);

    // Кнопка 3: Копировать ссылку
    const copyLinkBtn = document.createElement('button');
    copyLinkBtn.textContent = '🔗 Ссылка';
    copyLinkBtn.className = 'small-btn';
    copyLinkBtn.onclick = () => copyTextToClipboard(file.https_url, 'Ссылка скопирована!');
    actions.appendChild(copyLinkBtn);

    // Кнопка 4: Скачать фото
    const downloadBtn = document.createElement('button');
    downloadBtn.textContent = '⬇️ Скачать';
    downloadBtn.className = 'small-btn';
    const downloadUrl = `${API_BASE_URL}${file.preview_url}?download=true`;
    downloadBtn.onclick = () => downloadFile(downloadUrl, file.name);
    actions.appendChild(downloadBtn);

    item.appendChild(actions);
    return item;
}

// --- 3. Функции копирования/скачивания (без изменений) ---

// Копирование текста в буфер обмена
function copyTextToClipboard(text, successMessage = 'Текст скопирован!') {
    navigator.clipboard.writeText(text)
        .then(() => {
            messageElement.textContent = successMessage;
            setTimeout(() => messageElement.textContent = '', 2000);
        })
        .catch(err => {
            console.error('Ошибка копирования:', err);
            messageElement.textContent = 'Ошибка копирования!';
        });
}


// Функция: Скачивание файла
function downloadFile(url, filename) {
    try {
        messageElement.textContent = 'Инициирую скачивание...';

        const link = document.createElement('a');
        link.href = url;
        link.download = filename;

        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        messageElement.textContent = 'Скачивание начато!';
        setTimeout(() => messageElement.textContent = '', 2000);

    } catch (err) {
        console.error('Ошибка скачивания:', err);
        messageElement.textContent = 'Ошибка скачивания файла!';
    }
}

// --- 4. Обработчики событий ---

// Поиск с задержкой (debounce)
let searchTimeout;
searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        currentQuery = searchInput.value;
        // НОВОЕ: Перезагружаем с 1 страницы, используя текущее состояние фильтра
        loadPhotos(1, currentQuery, isStockFilterActive);
    }, 500);
});

// НОВОЕ: Обработчик для чекбокса "В наличии"
stockCheckbox.addEventListener('change', () => {
    isStockFilterActive = stockCheckbox.checked;
    // НОВОЕ: Перезагружаем с 1 страницы, используя текущий запрос и новое состояние фильтра
    loadPhotos(1, currentQuery, isStockFilterActive);
});

// Пагинация (Обновлена для использования isStockFilterActive)
prevBtn.addEventListener('click', () => {
    if (currentPage > 1) {
        loadPhotos(currentPage - 1, currentQuery, isStockFilterActive);
    }
});

nextBtn.addEventListener('click', () => {
    if (currentPage < totalPages) {
        loadPhotos(currentPage + 1, currentQuery, isStockFilterActive);
    }
});

// Инициализация (Обновлена для использования isStockFilterActive)
document.addEventListener('DOMContentLoaded', () => {
    loadPhotos(currentPage, currentQuery, isStockFilterActive);
});
