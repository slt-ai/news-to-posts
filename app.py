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

import os  # читаем настройки из .env

import requests  # ходим на сайты и в API модели
from bs4 import BeautifulSoup  # вытаскиваем текст из HTML страницы товара
from dotenv import load_dotenv  # подключаем чтение файла .env
from flask import Flask, render_template, request

# Читаем настройки запуска из файла .env (ключ API, хост, порт, debug)
load_dotenv()

app = Flask(__name__)

# Путь к файлу настроек «голоса» поста. Кладём рядом с app.py.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # папка проекта
VOICE_FILE = os.path.join(BASE_DIR, "voice.md")


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
                key = key.strip().upper()

                if key in KEY_MAP:
                    field = KEY_MAP[key]
                    value = value.strip()

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
# ЧАСТЬ 3. СКАЧИВАНИЕ СТРАНИЦЫ ТОВАРА
# ============================================================

class LinkError(Exception):
    """Ошибка: не получилось открыть страницу товара."""


class ModelError(Exception):
    """Ошибка: модель не ответила или API недоступен."""


def fetch_product_page(url):
    """
    Открывает ссылку на товар и возвращает словарь:
    {"title": "название страницы", "text": "текст страницы"}.

    Некоторые магазины защищаются от роботов (код 403/498) или строят
    страницы на JavaScript — тогда текст может быть пустым. В этом
    случае пользователь может добавить описание товара своими словами,
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
    """Сообщение пользователя: то, что модель знает о товаре."""

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
    (сайт защищён от ботов), но пользователь описал товар своими словами.
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
# ЧАСТЬ 5. СТРАНИЦЫ САЙТА
# ============================================================

@app.route("/")
def index():
    """Главная страница с формой. Обычный GET-запрос."""
    return render_template(
"index.html",
        post=None,
        error=None,
        form=None,
        page=None,
        emotions=EMOTIONS,
        models=CLOUD_MODELS,
        default_model=get_default_model(),
    )


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
            post=None,
            error="Пожалуйста, вставь ссылку на новость или статью.",
            form=form_data,
            page=None,
            notice=None,
            emotions=EMOTIONS,
            models=CLOUD_MODELS,
            default_model=get_default_model(),
        )

    # Настройки голоса из voice.md
    settings = load_voice_settings()

    # Шаг 1: пробуем скачать страницу товара
    page = None
    fetch_failed_reason = None
    try:
        page = fetch_product_page(url)
    except LinkError as err:
        # Запомнили причину: если пользователь вписал описание своими
        # словами, пост можно собрать и без открытия страницы.
        fetch_failed_reason = str(err)

    # Если страница не открылась и пользователь не добавил описание —
    # показываем ошибку и просим дополнить поле.
    if page is None and not extra_note:
        return render_template(
            "index.html",
            post=None,
            error=fetch_failed_reason
            + " Опиши суть новости своими словами в поле «Дополнительно о новости» — "
            + "тогда пост соберётся по твоему описанию.",
            form=form_data,
            page=None,
            notice=None,
            emotions=EMOTIONS,
            models=CLOUD_MODELS,
            default_model=get_default_model(),
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
            post=None,
            error=str(err),
            form=form_data,
            page=page,
            notice=notice,
            emotions=EMOTIONS,
            models=CLOUD_MODELS,
            default_model=get_default_model(),
        )

    return render_template(
        "index.html",
        post=post,
        error=None,
        form=form_data,
        page=page,
        notice=notice,
        emotions=EMOTIONS,
        models=CLOUD_MODELS,
        default_model=get_default_model(),
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
