const API_BASE_URL = "http://37.220.81.157:8088";
let currentPage = 1;
let currentQuery = "";
let totalPages = 1;

const grid = document.getElementById('photos-grid');
const searchInput = document.getElementById('search-input');
const prevBtn = document.getElementById('prev-page');
const nextBtn = document.getElementById('next-page');
const pageInfo = document.getElementById('page-info');
const messageElement = document.getElementById('message');

// --- 1. Основная функция загрузки данных ---
async function loadPhotos(page, query) {
    messageElement.textContent = 'Загрузка...';
    grid.innerHTML = '';

    try {
        const response = await fetch(`${API_BASE_URL}/api/list?page=${page}&query=${query}`);

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
    item.className = 'photo-item';

    // Изображение
    const img = document.createElement('img');
    img.src = `${API_BASE_URL}${file.preview_url}`;
    img.alt = file.name;
    item.appendChild(img);

    // Имя файла
    const name = document.createElement('p');
    name.textContent = file.name;
    name.title = file.name;
    item.appendChild(name);

    // Кнопки действий
    const actions = document.createElement('div');
    actions.className = 'actions';

    // Кнопка 1: Копировать ссылку
    const copyLinkBtn = document.createElement('button');
    copyLinkBtn.textContent = '🔗 Ссылка';
    copyLinkBtn.onclick = () => copyTextToClipboard(file.https_url);
    actions.appendChild(copyLinkBtn);

    // Кнопка 3: Скачать фото
    const downloadBtn = document.createElement('button');
    downloadBtn.textContent = '⬇️ Скачать';
    const downloadUrl = `${API_BASE_URL}${file.preview_url}?download=true`;
    downloadBtn.onclick = () => downloadFile(downloadUrl, file.name);
    actions.appendChild(downloadBtn);

    item.appendChild(actions);
    return item;
}

// --- 3. Функции копирования/скачивания ---

// Копирование текста в буфер обмена
function copyTextToClipboard(text) {
    navigator.clipboard.writeText(text)
        .then(() => {
            messageElement.textContent = 'Ссылка скопирована!';
            setTimeout(() => messageElement.textContent = '', 2000);
        })
        .catch(err => {
            console.error('Ошибка копирования ссылки:', err);
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
        loadPhotos(1, currentQuery);
    }, 500);
});

// Пагинация
prevBtn.addEventListener('click', () => {
    if (currentPage > 1) {
        loadPhotos(currentPage - 1, currentQuery);
    }
});

nextBtn.addEventListener('click', () => {
    if (currentPage < totalPages) {
        loadPhotos(currentPage + 1, currentQuery);
    }
});

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    loadPhotos(currentPage, currentQuery);
});