"""
Генератор постов по новостям и статьям на тему ИИ и компьютерных
технологий (Flask + языковая модель Ollama Cloud).
Локальный запуск:  python app.py
Потом открой в браузере:  http://127.0.0.1:5000

Как это работает:
  1. Пользователь вставляет ссылку на новость или статью.
  2. Сервер скачивает страницу и достаёт из неё текст.
  3. Собранный текст + настройки из voice.md + выбранное настроение
     отправляются облачной языковой модели Ollama (https://ollama.com/api).
  4. Модель пишет оригинальный пост, страница показывает его.
  5. Кнопка «Скопировать пост» копирует текст в буфер обмена.

Авторизация в Ollama Cloud:
  - Нужен API-ключ: https://ollama.com/settings/keys
  - Ключ кладём в файл .env → OLLAMA_API_KEY=...
  - Модели и цены: https://ollama.com/pricing (цены за миллион токенов)
  - Документация: https://docs.ollama.com/cloud и https://docs.ollama.com/api/chat
"""

import json  # храним избранное в JSON-файле
import os  # читаем настройки из .env
import time  # для отложенных постов (unix-время)
import uuid  # уникальные id для избранного
from datetime import datetime  # разбор даты отложенного поста

import requests  # ходим на сайты и в API модели
from bs4 import BeautifulSoup  # вытаскиваем текст из HTML страницы новости
from dotenv import load_dotenv  # подключаем чтение файла .env
from flask import Flask, redirect, render_template, request, url_for

# Читаем настройки запуска из файла .env (ключ API, хост, порт, debug)
load_dotenv()

app = Flask(__name__)

# Путь к файлу настроек «голоса» поста. Кладём рядом с app.py.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # папка проекта
VOICE_FILE = os.path.join(BASE_DIR, "voice.md")

# Папка и файл для избранного. JSON — проще всего, без базы данных.
DATA_DIR = os.path.join(BASE_DIR, "data")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")

# Версия API ВКонтакте. 5.199 — стабильная на 2025–2026 годы.
VK_API_VERSION = "5.199"


# ============================================================
# ЧАСТЬ 1. НАСТРОЙКИ ГОЛОСА ПОСТА (файл voice.md)
# ============================================================

# Словарь-подсказка: как слова из voice.md превращаются в названия настроек.
KEY_MAP = {
    "ТОН": "tone",                  # общий тон поста
    "ДЛИНА": "length",              # длина поста
    "КОЛИЧЕСТВО": "emoji_count",    # количество эмодзи
    "СТИЛЬ": "headline_style",      # стиль заголовка
}


def load_voice_settings():
    """
    Читает файл voice.md и возвращает словарь с настройками.

    Эти настройки вставляются в задание для языковой модели.
    Если файл не найден или значение непонятное — используется
    значение по умолчанию, чтобы ничего не сломалось.
    """
    settings = {
        "tone": "дружелюбный",
        "length": "средний",
        "emoji_count": 3,
        "headline_style": "заглавные",
    }

    if not os.path.exists(VOICE_FILE):
        return settings  # файла нет — берём стандартные настройки

    with open(VOICE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Пропускаем пустые строки и комментарии (строки с #)
            if not line or line.startswith("#"):
                continue

            if ":" in line:
                key, value = line.split(":", 1)
                full_key = key.strip().upper()

                # В voice.md ключ может быть полным: «ТОН ПОСТА», а в KEY_MAP
                # коротким: «ТОН». Ищем совпадение по подстроке первой части.
                field = None
                for map_key, map_field in KEY_MAP.items():
                    if map_key in full_key:
                        field = map_field
                        break

                if field:
                    value = value.strip()
                    # Отрезаем инлайновый комментарий: «дружелюбный # пояснение»
                    value = value.split("#", 1)[0].strip()

                    if field == "emoji_count":
                        # Количество эмодзи делаем числом от 0 до 5
                        try:
                            settings[field] = max(0, min(5, int(value)))
                        except ValueError:
                            pass  # написали не число — оставляем по умолчанию
                    else:
                        settings[field] = value

    return settings


# ============================================================
# ЧАСТЬ 2. МОДЕЛИ OLLAMA CLOUD И НАСТРОЕНИЯ ПОСТА
# ============================================================

# Облачные модели с ценами за миллион токенов (вход / выход).
# Полный список и цены: https://ollama.com/pricing
#
# ВАЖНО: у части моделей есть режим «мышления» (thinking), из-за него
# модель тратит лимит токенов на внутренние рассуждения и может выдать
# пустой ответ или сунуть рассуждения в сам пост. Для этой задачи
# лучший выбор — gemma4: пишет готовый пост сразу, без «дум».
CLOUD_MODELS = [
    {"id": "gemma4",            "name": "Gemma 4",           "price": "$0.14 / $0.40"},   # рекомендуемая: пост сразу
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "price": "$0.22 / $0.66"},   # тоже выдаёт пост сразу
    {"id": "gpt-oss:120b",      "name": "GPT-OSS 120B",      "price": "$0.15 / $0.60"},   # думает дольше, может не уложиться
    {"id": "mistral-large-3",   "name": "Mistral Large 3",   "price": "$0.50 / $1.50"},
    {"id": "kimi-k3",           "name": "Kimi K3",            "price": "$3.00 / $15.00"},  # топовая, но дорогая
    {"id": "glm-5.3-flash",     "name": "GLM 5.3 Flash",     "price": "$0.15 / $0.50"},   # не рекомендуется: рассуждения попадают в пост
]


def get_default_model():
    """Модель по умолчанию: из .env (OLLAMA_MODEL) или первая из списка."""
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if any(m["id"] == model for m in CLOUD_MODELS):
        return model
    return CLOUD_MODELS[0]["id"]


# Настроения поста. Каждое настроение — это описание для модели,
# которого она должна придерживаться в тексте.
EMOTIONS = {
    "joy": {
        "label": "Радостное 🎉",
        "hint": "позитивное, лёгкое, с искоркой счастья и улыбкой",
    },
    "excited": {
        "label": "Восторженное 🤩",
        "hint": "полное восторга и энергии, будто нашли невероятную находку",
    },
    "inspiring": {
        "label": "Вдохновляющее 🌟",
        "hint": "мотивирующее, обращается к мечтам, целям и новым возможностям",
    },
    "playful": {
        "label": "Игривое 😜",
        "hint": "с юмором, лёгкой самоиронией и уместными шутками",
    },
    "mysterious": {
        "label": "Интригующее 🔮",
        "hint": "с интригой и лёгкой недосказанностью, вызывает любопытство",
    },
    "business": {
        "label": "Деловое 💼",
        "hint": "сдержанное, уверенное, по существу, минимум воды",
    },
}

# Короткое описание длины, чтобы модель понимала объём текста.
LENGTH_HINTS = {
    "короткий": "короткий пост: 2–3 коротких абзаца",
    "средний": "средний пост: 3–4 абзаца",
    "длинный": "длинный пост: 4–6 абзацев",
}


# ============================================================
# ЧАСТЬ 3. СКАЧИВАНИЕ СТРАНИЦЫ НОВОСТИ
# ============================================================

class LinkError(Exception):
    """Ошибка: не получилось открыть страницу сайта."""


class ModelError(Exception):
    """Ошибка: модель не ответила или API недоступен."""


def fetch_news_page(url):
    """
    Открывает ссылку на новость и возвращает словарь:
    {"title": "название страницы", "text": "текст страницы"}.

    Некоторые сайты защищаются от роботов (код 403/498) или строят
    страницы на JavaScript — тогда текст может быть пустым. В этом
    случае пользователь может добавить описание новости своими словами,
    а его пост будет собран по этому описанию (см. generate()).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()  # поднимем ошибку, если сайт не отвечает
    except requests.RequestException as err:
        # Коды 401, 403 и 498 обычно означают, что сайт защищается от роботов
        status = getattr(err, "response", None)
        status_code = status.status_code if status is not None else None
        if status_code in (401, 403, 498):
            raise LinkError(
                "Сайт запретил автоматическое чтение страницы "
                f"(код {status_code}) — он защищён от ботов."
            )
        raise LinkError(f"Не удалось открыть ссылку: {err}")

    # Разбираем HTML и убираем бесполезные части (скрипты, меню и т.п.)
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else "Без названия"
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()

    # Весь текст страницы одним куском. Переносы строк схлопываем в пробелы.
    text = " ".join(soup.get_text(separator=" ").split())

    if not text:
        raise LinkError("На странице не нашлось текста — сайт мог закрыть доступ роботам.")

    # Ограничиваем размер текста, чтобы не тратить лишние токены модели.
    return {"title": title[:200], "text": text[:12000]}


# ============================================================
# ЧАСТЬ 4. ЗАПРОС К ЯЗЫКОВОЙ МОДЕЛИ
# ============================================================

def build_system_prompt(settings, emotion_id):
    """Системное задание для модели: голос, настроение, правила."""

    emotion = EMOTIONS[emotion_id]
    length_hint = LENGTH_HINTS.get(settings["length"], LENGTH_HINTS["средний"])

    # Стиль заголовка (первой строки поста)
    if settings["headline_style"] == "заглавные":
        headline_hint = "заголовок — короткий и броский, словами ЗАГЛАВНЫМИ буквами"
    else:
        headline_hint = "заголовок обычным регистром, без капса"

    return f"""Ты — креативный технический SMM-копирайтер. Твоя задача: написать ОДИН оригинальный
и интересный пост для соцсетей по новости или статье на тему ИИ и компьютерных технологий.

Правила:
- Пиши на русском языке.
- Передай суть новости коротко и понятно — даже тому, кто не в теме.
- Не выдумывай фактов, цифр и деталей, которых нет в тексте новости.
- Настроение поста: {emotion['hint']}.
- Общий тон: {settings['tone']}.
- Длина: {length_hint}.
- Количество эмодзи в посте: {settings['emoji_count']}, не разбрасывай эмодзи по каждому абзацу.
- {headline_hint}.
- В конце поста добавь 2–4 подходящих хештега (#ИИ, #технологии и подобные).
- Разбивай текст на абзацы с пустыми строками между ними.
- Верни только текст поста, без пояснений и вступлений."""


def build_user_prompt(page, extra_note, url):
    """Сообщение пользователя: то, что модель знает о новости."""

    text = page["text"]
    if extra_note.strip():
        text += f"\nДополнительная информация от пользователя: {extra_note.strip()}"

    return f"""Вот новость или статья со страницы {url}:

Название страницы: {page['title']}

Содержимое страницы:
{text}

Напиши готовый пост для соцсетей. Только текст поста."""


def build_user_prompt_from_note(extra_note, url):
    """
    Сообщение пользователя для случая, когда страница не открылась
    (сайт защищён от ботов), но пользователь описал новость своими словами.
    Пишем пост по этому описанию.
    """
    return f"""Ссылка на страницу {url} не открылась, поэтому суть новости пользователь описал своими словами:

{extra_note.strip()}

Напиши готовый пост для соцсетей. Только текст поста."""


def build_api_error(response):
    """
    Превращает ошибочный ответ API модели в понятную подсказку.
    Смотрим на код ответа (401, 403, ...) и текст ошибки от сервера.
    """
    # Достаём текст ошибки из тела ответа (обычно поле "error")
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("message") or "").strip()
    except ValueError:
        pass

    extra = f" Ответ сервера: {detail}" if detail else ""
    status = response.status_code

    if status == 401:
        return (
            "Похоже, ключ API неправильный или неактивный (ошибка 401). "
            "Проверь OLLAMA_API_KEY в файле .env: без лишних пробелов, кавычек "
            "и пустых строк вокруг. Если не помогло — создай новый ключ на "
            "https://ollama.com/settings/keys и вставь его."
        ) + extra

    if status == 403:
        return (
            "Эта модель недоступна на твоём тарифе (ошибка 403). На бесплатном "
            "плане работают только starter-модели. Выбери другую модель в списке "
            "на странице или пополни кредиты: https://ollama.com/upgrade"
        ) + extra

    if status == 429:
        return (
            "Слишком много запросов или закончились кредиты (ошибка 429). "
            "Подожди немного или проверь баланс: https://ollama.com/settings"
        ) + extra

    return f"Модель вернула ошибку HTTP {status}." + extra


def ask_ollama(system_prompt, user_prompt, model):
    """
    Отправляет задание в Ollama Cloud и возвращает готовый текст поста.

    Адрес API: {OLLAMA_HOST}/api/chat
    Авторизация: Bearer-токен (ключ из .env → OLLAMA_API_KEY)
    Формат запроса описан в https://docs.ollama.com/api/chat
    """

    # Проверяем, что ключ заполнен
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    if not api_key:
        raise ModelError(
            "В файле .env не заполнен OLLAMA_API_KEY. "
            "Создай ключ на https://ollama.com/settings/keys и вставь его туда."
        )

    host = os.getenv("OLLAMA_HOST", "https://ollama.com").rstrip("/")
    url = f"{host}/api/chat"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,  # получаем готовый ответ целиком
        # Отключаем «мышление» модели: для поста оно не нужно,
        # зато ответ приходит быстрее и не тратит токены.
        "think": False,
        "options": {
            "temperature": 0.9,  # повыше — чтобы посты получались разными
            "num_predict": 600,  # максимум токенов в ответе
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180)
    except requests.RequestException as err:
        raise ModelError(f"Запрос к модели не удался: {err}")

    # Если API вернул ошибку (401, 403, 429, ...) — объясняем её пользователю
    if response.status_code != 200:
        raise ModelError(build_api_error(response))

    data = response.json()

    # Ответ модели лежит в data["message"]["content"]
    content = (data.get("message") or {}).get("content", "").strip()
    if not content:
        raise ModelError("Модель вернула пустой ответ. Попробуй ещё раз.")

    return content


# ============================================================
# ЧАСТЬ 5. ВКОНТАКТЕ: ПУБЛИКАЦИЯ И ОТЛОЖЕННЫЕ ПОСТЫ
# ============================================================
# Как это работает простыми словами:
# - У сообщества ВК есть числовой ID (например 123456789).
# - У тебя есть секретный ключ (токен) — как пароль для программы.
# - Программа говорит ВК: «опубликуй этот текст на стене -ID»,
#   ВК слушается, если ключ правильный и у него есть права.
# - Если указать время в будущем (publish_date), ВК сам
#   придержит пост и выложит его позже — это и есть «отложка».
# Документация: https://dev.vk.com/ru/method/wall.post


class VkError(Exception):
    """Ошибка: не получилось поговорить с ВКонтакте."""


def get_vk_settings():
    """
    Читает настройки ВК из .env и говорит, всё ли заполнено.

    Возвращает словарь:
    {"token": "...", "group_id": "...", "configured": True/False}
    """
    token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    group_id = os.getenv("VK_GROUP_ID", "").strip()
    # Убираем минус в начале, если пользователь вписал -12345
    group_id = group_id.lstrip("-").strip()
    configured = bool(token and group_id and group_id.isdigit())
    return {"token": token, "group_id": group_id, "configured": configured}


def vk_api(method, params):
    """
    Один вызов метода VK API. Например vk_api("wall.post", {...}).

    Если ВК вернул {"error": {...}} — превращаем это в понятный VkError.
    """
    vk = get_vk_settings()
    if not vk["configured"]:
        raise VkError(
            "ВКонтакте не подключён: заполни VK_ACCESS_TOKEN и VK_GROUP_ID "
            "в файле .env (подробности — в файле ВК_ИНСТРУКЦИЯ.md)."
        )
    url = f"https://api.vk.com/method/{method}"
    payload = dict(params)
    payload["access_token"] = vk["token"]
    payload["v"] = VK_API_VERSION
    try:
        response = requests.post(url, data=payload, timeout=20)
        response.raise_for_status()
    except requests.RequestException as err:
        raise VkError(f"Не получилось связаться с ВКонтакте: {err}")
    try:
        data = response.json()
    except ValueError:
        raise VkError("ВКонтакте вернул непонятный ответ (не JSON).")
    if "error" in data:
        err = data["error"]
        code = err.get("error_code", "?")
        msg = err.get("error_msg", "неизвестная ошибка")
        # Частые коды — объясняем по-человечески
        hints = {
            5: "ключ (токен) неправильный или просрочен — создай новый",
            7: "нет прав на это действие — выдай токену права на стену и фото",
            15: "доступ запрещён — проверь, что ты админ сообщества",
            100: "неверный параметр — обычно это ID сообщества",
            214: "постить на стену запрещено — включи стену в настройках сообщества",
        }
        hint = hints.get(code, "")
        extra = f" Подсказка: {hint}." if hint else ""
        raise VkError(f"ВКонтакте ответил ошибкой {code}: {msg}.{extra}")
    return data.get("response", {})


def vk_publish_post(text, publish_timestamp=None):
    """
    Публикует текст на стене сообщества.

    - text: текст поста (не пустой).
    - publish_timestamp: unix-время (секунды) для отложки или None = сейчас.
      ВК требует, чтобы отложка была минимум на 2 минуты позже «сейчас»
      и не позже чем через год.
    Возвращает id созданного поста в ВК.
    """
    text = (text or "").strip()
    if not text:
        raise VkError("Текст поста пустой — нечего публиковать.")
    vk = get_vk_settings()
    params = {
        "owner_id": f"-{vk['group_id']}",  # минус = стена сообщества, а не человека
        "from_group": 1,  # пост от имени сообщества
        "message": text,
    }
    if publish_timestamp:
        now = int(time.time())
        if publish_timestamp <= now + 2 * 60:
            raise VkError("Выбери время хотя бы на 2 минуты позже текущего.")
        if publish_timestamp > now + 365 * 24 * 3600:
            raise VkError("ВКонтакте не откладывает посты больше чем на год вперёд.")
        params["publish_date"] = int(publish_timestamp)
    result = vk_api("wall.post", params)
    return result.get("post_id")


def vk_get_postponed(count=20):
    """
    Возвращает список отложенных постов сообщества (их держит сам ВК).
    Каждый элемент: {"id": ..., "text": ..., "date": unix-время}.
    """
    vk = get_vk_settings()
    if not vk["configured"]:
        return []
    result = vk_api(
        "wall.get",
        {
            "owner_id": f"-{vk['group_id']}",
            "filter": "postponed",  # только отложенные
            "count": max(1, min(50, count)),
        },
    )
    items = result.get("items", []) if isinstance(result, dict) else []
    postponed = []
    for item in items:
        postponed.append(
            {
                "id": item.get("id"),
                "text": item.get("text", ""),
                "date": item.get("date"),
            }
        )
    # Сортируем по дате: ближайшие сверху
    postponed.sort(key=lambda p: p["date"] or 0)
    return postponed


def vk_delete_post(post_id):
    """Удаляет (отменяет) отложенный пост по его id."""
    vk = get_vk_settings()
    vk_api("wall.delete", {"owner_id": f"-{vk['group_id']}", "post_id": int(post_id)})


# ============================================================
# ЧАСТЬ 6. ИЗБРАННОЕ (сохранение постов локально)
# ============================================================
# Избранное хранится в файле data/favorites.json — это просто список.
# База данных не нужна: постов немного, JSON хватает за глаза.


def load_favorites():
    """Читает файл избранного. Если файла нет — возвращает пустой список."""
    if not os.path.exists(FAVORITES_FILE):
        return []
    try:
        with open(FAVORITES_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []  # файл битый — начинаем с чистого листа


def save_favorites(favorites):
    """Сохраняет список избранного в файл."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(favorites, f, ensure_ascii=False, indent=2)


def add_favorite(text, url="", emotion=""):
    """Добавляет пост в избранное, возвращает созданную запись."""
    favorites = load_favorites()
    entry = {
        "id": uuid.uuid4().hex[:8],  # короткий случайный id
        "text": (text or "").strip(),
        "url": (url or "").strip(),
        "emotion": emotion,
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    favorites.insert(0, entry)  # новые — сверху
    save_favorites(favorites)
    return entry


def remove_favorite(fav_id):
    """Удаляет пост из избранного по id."""
    favorites = [f for f in load_favorites() if f.get("id") != fav_id]
    save_favorites(favorites)


@app.template_filter("vkdate")
def vkdate_filter(timestamp):
    """Превращает unix-время ВК в «15.09.2026 в 18:30»."""
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%d.%m.%Y в %H:%M")
    except (ValueError, TypeError, OSError):
        return "—"


def build_page_context(**overrides):
    """
    Собирает общие данные для шаблона: избранное, отложка, статус ВК.
    Чтобы не дублировать код в каждом роуте.
    """
    vk = get_vk_settings()
    try:
        postponed = vk_get_postponed() if vk["configured"] else []
        postponed_error = None
    except VkError as err:
        postponed = []
        postponed_error = str(err)
    context = {
        "emotions": EMOTIONS,
        "models": CLOUD_MODELS,
        "default_model": get_default_model(),
        "vk_configured": vk["configured"],
        "vk_group_id": vk["group_id"],
        "favorites": load_favorites(),
        "postponed": postponed,
        "postponed_error": postponed_error,
        "post": None,
        "error": None,
        "notice": None,
        "vk_msg": None,
        "fav_msg": None,
        "form": None,
        "page": None,
    }
    context.update(overrides)
    return context


# ============================================================
# ЧАСТЬ 7. СТРАНИЦЫ САЙТА
# ============================================================

@app.route("/")
def index():
    """Главная страница с формой. Обычный GET-запрос."""
    return render_template("index.html", **build_page_context())


@app.route("/generate", methods=["POST"])
def generate():
    """Принимает форму и возвращает страницу с готовым постом от модели."""

    model = get_default_model()
    emotion_id = "joy"

    # Всё, что написал пользователь, приходит в request.form
    url = request.form.get("url", "").strip()
    extra_note = request.form.get("extra", "").strip()
    emotion_id = request.form.get("emotion", "joy")
    if emotion_id not in EMOTIONS:
        emotion_id = "joy"

    model = request.form.get("model", get_default_model())
    if not any(m["id"] == model for m in CLOUD_MODELS):
        model = get_default_model()

    # Возвращаем в форму то, что пользователь уже ввёл (чтобы не стёрлось).
    form_data = {
        "url": url,
        "extra": extra_note,
        "emotion": emotion_id,
        "model": model,
    }

# Ссылка — обязательное поле
    if not url:
        return render_template(
            "index.html",
            **build_page_context(
                post=None,
                error="Пожалуйста, вставь ссылку на новость или статью.",
                form=form_data,
                page=None,
                notice=None,
            ),
        )

    # Настройки голоса из voice.md
    settings = load_voice_settings()

    # Шаг 1: пробуем скачать страницу новости
    page = None
    fetch_failed_reason = None
    try:
        page = fetch_news_page(url)
    except LinkError as err:
        # Запомнили причину: если пользователь вписал описание своими
        # словами, пост можно собрать и без открытия страницы.
        fetch_failed_reason = str(err)

    # Если страница не открылась и пользователь не добавил описание —
    # показываем ошибку и просим дополнить поле.
    if page is None and not extra_note:
        return render_template(
            "index.html",
            **build_page_context(
                post=None,
                error=fetch_failed_reason
                + " Опиши суть новости своими словами в поле «Дополнительно о новости» — "
                + "тогда пост соберётся по твоему описанию.",
                form=form_data,
                page=None,
                notice=None,
            ),
        )

    # Шаг 2: собираем задание для модели
    system_prompt = build_system_prompt(settings, emotion_id)

    if page is not None:
        # Страница открылась — модель читает её текст + заметку пользователя
        user_prompt = build_user_prompt(page, extra_note, url)
        notice = None
    else:
        # Страница не открылась, но есть описание от пользователя
        user_prompt = build_user_prompt_from_note(extra_note, url)
        notice = "Не удалось открыть страницу сайта — пост собран по твоему описанию новости."

    # Шаг 3: просим модель написать пост
    try:
        post = ask_ollama(system_prompt, user_prompt, model)
    except ModelError as err:
        return render_template(
            "index.html",
            **build_page_context(
                post=None,
                error=str(err),
                form=form_data,
                page=page,
                notice=notice,
            ),
        )

    return render_template(
        "index.html",
        **build_page_context(
            post=post,
            error=None,
            form=form_data,
            page=page,
            notice=notice,
        ),
    )


@app.route("/publish", methods=["POST"])
def publish():
    """
    Кнопка «Опубликовать пост»: шлёт текст на стену сообщества ВК прямо сейчас.
    Текст берём из скрытого поля формы (сгенерированный пост).
    """
    text = request.form.get("post", "").strip()
    form_data = {
        "url": request.form.get("url", ""),
        "extra": request.form.get("extra", ""),
        "emotion": request.form.get("emotion", "joy"),
        "model": request.form.get("model", get_default_model()),
    }
    if not text:
        return render_template(
            "index.html",
            **build_page_context(post=None, error="Нечего публиковать: текст поста пустой.", form=form_data),
        )
    try:
        post_id = vk_publish_post(text)
    except VkError as err:
        # Пост не потерялся: возвращаем его обратно на страницу вместе с ошибкой
        return render_template(
            "index.html", **build_page_context(post=text, error=str(err), form=form_data)
        )
    return render_template(
        "index.html",
        **build_page_context(
            post=text,
            form=form_data,
            vk_msg=f"Пост опубликован в сообществе! ID поста в ВК: {post_id} 🎉",
        ),
    )


@app.route("/schedule", methods=["POST"])
def schedule():
    """
    Отложенный пост: публикуем в ВК с publish_date.
    Дату и время берём из поля datetime-local (вид «2026-09-15T18:30»).
    ВК сам выложит пост в это время — наш сервер для этого не нужен.
    """
    text = request.form.get("post", "").strip()
    publish_time_str = request.form.get("publish_time", "").strip()
    form_data = {
        "url": request.form.get("url", ""),
        "extra": request.form.get("extra", ""),
        "emotion": request.form.get("emotion", "joy"),
        "model": request.form.get("model", get_default_model()),
    }
    if not text:
        return render_template(
            "index.html",
            **build_page_context(post=None, error="Нечего планировать: текст поста пустой.", form=form_data),
        )
    if not publish_time_str:
        return render_template(
            "index.html",
            **build_page_context(post=text, error="Выбери дату и время для отложенного поста.", form=form_data),
        )
    # Разбираем «2026-09-15T18:30» в unix-время
    try:
        chosen = datetime.strptime(publish_time_str, "%Y-%m-%dT%H:%M")
        publish_timestamp = int(chosen.timestamp())
    except ValueError:
        return render_template(
            "index.html",
            **build_page_context(post=text, error="Непонятная дата. Выбери её через календарь.", form=form_data),
        )
    try:
        post_id = vk_publish_post(text, publish_timestamp=publish_timestamp)
    except VkError as err:
        return render_template(
            "index.html", **build_page_context(post=text, error=str(err), form=form_data)
        )
    nice_date = chosen.strftime("%d.%m.%Y в %H:%M")
    return render_template(
        "index.html",
        **build_page_context(
            post=text,
            form=form_data,
            vk_msg=f"Отложенный пост запланирован на {nice_date}! ID в ВК: {post_id} ⏰",
        ),
    )


@app.route("/scheduled/delete", methods=["POST"])
def scheduled_delete():
    """Отмена отложенного поста (удаляет его из очереди ВК)."""
    post_id = request.form.get("post_id", "").strip()
    # Сохраняем текущий пост на экране, чтобы он не пропал
    current_post = request.form.get("post", "")
    form_data = {
        "url": request.form.get("url", ""),
        "extra": request.form.get("extra", ""),
        "emotion": request.form.get("emotion", "joy"),
        "model": request.form.get("model", get_default_model()),
    }
    try:
        vk_delete_post(post_id)
        msg = f"Отложенный пост {post_id} отменён."
    except (VkError, ValueError) as err:
        return render_template(
            "index.html",
            **build_page_context(post=current_post or None, error=str(err), form=form_data or None),
        )
    return render_template(
        "index.html",
        **build_page_context(post=current_post or None, form=form_data or None, vk_msg=msg),
    )


@app.route("/favorites/add", methods=["POST"])
def favorites_add():
    """Кнопка «В избранное»: сохраняет текст поста в data/favorites.json."""
    text = request.form.get("post", "").strip()
    form_data = {
        "url": request.form.get("url", ""),
        "extra": request.form.get("extra", ""),
        "emotion": request.form.get("emotion", "joy"),
        "model": request.form.get("model", get_default_model()),
    }
    if not text:
        return render_template(
            "index.html",
            **build_page_context(post=None, error="Нечего сохранять: текст поста пустой.", form=form_data),
        )
    # Не плодим дубли: если такой текст уже есть — просто скажем об этом
    if any(f.get("text") == text for f in load_favorites()):
        return render_template(
            "index.html",
            **build_page_context(post=text, form=form_data, fav_msg="Этот пост уже есть в избранном ⭐"),
        )
    add_favorite(text, url=form_data["url"], emotion=form_data["emotion"])
    return render_template(
        "index.html",
        **build_page_context(post=text, form=form_data, fav_msg="Сохранено в избранное ⭐"),
    )


@app.route("/favorites/delete", methods=["POST"])
def favorites_delete():
    """Удаляет пост из избранного по его id."""
    fav_id = request.form.get("fav_id", "").strip()
    current_post = request.form.get("post", "")
    form_data = {
        "url": request.form.get("url", ""),
        "extra": request.form.get("extra", ""),
        "emotion": request.form.get("emotion", "joy"),
        "model": request.form.get("model", get_default_model()),
    }
    if fav_id:
        remove_favorite(fav_id)
    return render_template(
        "index.html",
        **build_page_context(
            post=current_post or None,
            form=form_data if any(form_data.values()) else None,
            fav_msg="Удалено из избранного.",
        ),
    )


# ============================================================
# ЗАПУСК САЙТА
# ============================================================

if __name__ == "__main__":
    # Настройки берутся из файла .env (см. описание переменных в нём).
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),  # на каком адресе слушать сайт
        port=int(os.getenv("PORT", "5000")),  # на каком порту слушать сайт
        # debug=True — автоматически перезагружает сервер при изменениях.
        # Ставим "0", если нужен обычный режим без автоперезагрузки.
        debug=os.getenv("DEBUG", "1") == "1",
    )
