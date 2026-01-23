import asyncio, logging, os, re, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs, unquote
from io import BytesIO
import base64

load_dotenv()

from openai import OpenAI
from telethon.tl.types import User
from tdata_session_exporter import authorize_client

# Загрузка параметров из переменных окружения
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "api_key")
PROXIES             = os.getenv("PROXIES", "ansible.9qw.ru:8126:admin:password")
PROXY_TYPE          = os.getenv("PROXY_TYPE", "http")
CHECK_OLD_MESSAGES_LIMIT      = int(os.getenv("CHECK_OLD_MESSAGES_LIMIT", 20))
MESSAGES_LIMIT      = int(os.getenv("MESSAGES_LIMIT", 10))
MONITOR_INTERVAL    = int(os.getenv("MONITOR_INTERVAL", 30))
DIALOGS_LIMIT       = int(os.getenv("DIALOGS_LIMIT", 10))
DIALOGS_INTERVAL    = int(os.getenv("DIALOGS_INTERVAL", 5))
CHATGPT_LIMIT       = int(os.getenv("CHATGPT_LIMIT", 3))
CHATGPT_WAIT_LIMIT  = int(os.getenv("CHATGPT_WAIT_LIMIT", 60))
SEND_DELAYED        = int(os.getenv("SEND_DELAYED", '1'))
DELAY_MINUTES       = float(os.getenv("DELAY_MINUTES", '60'))
FORWARD_ENABLED     = int(os.getenv("FORWARD_ENABLED", '1'))
REPLY_COOLDOWN_DAYS     = int(os.getenv("REPLY_COOLDOWN_DAYS", '90'))
DELAYED_MESSAGE     = os.getenv("DELAYED_MESSAGE", "Приветствую, вы определились по заказу? Может доставку или самовывоз на сегодня?")
NON_TEXT_REPLY     = os.getenv("NON_TEXT_REPLY", "Добрый день, напишите пожалуйста текстом, где вы находитесь и какой товар вас интересует?")
FOLLOW_UP_MESSAGE     = os.getenv("FOLLOW_UP_MESSAGE", "Если у вас ещё остались какие-то вопросы, смело задавайте")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-1002510370326"))
FORWARD_WAIT_TIME = int(os.getenv("FORWARD_WAIT_TIME", "30"))
INITIAL_WAIT_TIME = int(os.getenv("INITIAL_WAIT_TIME", "60"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
SYSTEM_PROMPT_PATH = os.getenv("SYSTEM_PROMPT_PATH", "sessions/autoreply_prompt.txt")
PROMPT_URL = os.getenv("PROMPT_URL", "https://our-promts.fsn1.your-objectstorage.com/prompts/link.txt")
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "5120"))
OPENAI_RETRY_COUNT = int(os.getenv("OPENAI_RETRY_COUNT", "3"))
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
client = OpenAI(api_key=OPENAI_API_KEY)

# Настройка логгера
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_system_prompt():
    if not PROMPT_URL or not PROMPT_URL.strip():
        logger.error("PROMPT_URL не задан — завершение работы")
        raise RuntimeError("PROMPT_URL is required")
    logger.info("Загрузка системного промпта из URL: %s", PROMPT_URL)
    try:
        response = requests.get(PROMPT_URL.strip(), timeout=10)
        response.raise_for_status()
        text = response.text
        if not text or not text.strip():
            raise ValueError("Промпт пустой")
        logger.info("Системный промпт успешно загружен и отформатирован из URL: %s", PROMPT_URL)
        return text
    except Exception as e:
        logger.error("Не удалось загрузить системный промпт из URL %s: %s", PROMPT_URL, e)
        raise RuntimeError("SYSTEM_PROMPT is unavailable") from e

def update_system_prompt(dialog_id, user_name):
    """
    Обновляет системный промпт для нового диалога с проверками и логированием
    """
    global SYSTEM_PROMPT
    logger.info("Обновление системного промпта для диалога %s с пользователем '%s'", dialog_id, user_name)
    
    if not PROMPT_URL or not PROMPT_URL.strip():
        logger.warning("PROMPT_URL не задан - пропускаем обновление промпта для диалога %s", dialog_id)
        return False
    
    try:
        logger.info("Загружаем новый промпт из URL: %s для диалога %s", PROMPT_URL, dialog_id)
        response = requests.get(PROMPT_URL.strip(), timeout=10)
        response.raise_for_status()
        
        new_prompt = response.text
        if not new_prompt or not new_prompt.strip():
            logger.warning("Загруженный промпт пустой для диалога %s - оставляем текущий промпт", dialog_id)
            return False
        
        # Дополнительная проверка на минимальную длину промпта
        if len(new_prompt.strip()) < 10:
            logger.warning("Загруженный промпт слишком короткий (%d символов) для диалога %s - оставляем текущий промпт", 
                          len(new_prompt.strip()), dialog_id)
            return False
        
        old_prompt_length = len(SYSTEM_PROMPT) if SYSTEM_PROMPT else 0
        SYSTEM_PROMPT = new_prompt.strip()
        new_prompt_length = len(SYSTEM_PROMPT)
        
        logger.info("Системный промпт успешно обновлен для диалога %s с пользователем '%s' (длина: %d -> %d символов)", 
                   dialog_id, user_name, old_prompt_length, new_prompt_length)
        return True
        
    except requests.exceptions.Timeout:
        logger.error("Таймаут при загрузке промпта для диалога %s - оставляем текущий промпт", dialog_id)
        return False
    except requests.exceptions.ConnectionError:
        logger.error("Ошибка подключения при загрузке промпта для диалога %s - оставляем текущий промпт", dialog_id)
        return False
    except requests.exceptions.HTTPError as e:
        logger.error("HTTP ошибка %s при загрузке промпта для диалога %s - оставляем текущий промпт", e.response.status_code, dialog_id)
        return False
    except Exception as e:
        logger.error("Неожиданная ошибка при обновлении промпта для диалога %s: %s - оставляем текущий промпт", dialog_id, e)
        return False
SYSTEM_PROMPT = load_system_prompt()

# Утилита для доступа к полям словаря
class dotdict(dict):
    __getattr__ = dict.__getitem__



def extract_map_links(text):
    if not text:
        return []
    patterns = [
        r'https?://maps\.app\.goo\.gl/[A-Za-z0-9_-]+',
        r'https?://goo\.gl/maps/[A-Za-z0-9_-]+',
        r'https?://(?:www\.)?google\.[a-z.]+/maps[^\s]*',
        r'https?://maps\.google\.[a-z.]+/[^\s]*',
    ]
    links = []
    for pattern in patterns:
        links.extend(re.findall(pattern, text, re.IGNORECASE))
    cleaned = [re.sub(r'\?g_st=\w+$', '', link).rstrip('.,;:!?)>]') for link in links]
    return list(set(cleaned))


def resolve_google_maps_link(short_url):
    try:
        response = requests.head(short_url, allow_redirects=True, timeout=10)
        parsed = urlparse(response.url)
        params = parse_qs(parsed.query)

        if 'q' in params:
            return unquote(params['q'][0]).replace('+', ' ')
        if 'daddr' in params:
            return unquote(params['daddr'][0]).replace('+', ' ')

        place_match = re.search(r'/place/([^/@]+)', parsed.path)
        if place_match:
            return unquote(place_match.group(1)).replace('+', ' ')

        coords_match = re.search(r'@?(-?\d+\.\d+),(-?\d+\.\d+)', response.url)
        if coords_match:
            return f"{coords_match.group(1)}, {coords_match.group(2)}"
        return None
    except Exception as e:
        logger.error(f"Ошибка при резолве ссылки {short_url}: {e}")
        return None


def is_system_message(message):
    """
    Простая и надёжная проверка системных сообщений Telegram
    """
    # В Telethon системные сообщения помечены атрибутом service=True
    # (joined, left, pinned, title changed, photo changed и т.д.)
    if getattr(message, 'service', False):
        return True

    # Проверка на уведомления об упоминаниях в историях
    if hasattr(message, 'text') and message.text and 'mentioned you in a story' in message.text:
        return True

    # Проверка на истории (stories) - они имеют атрибут media с типом MessageMediaStory
    if hasattr(message, 'media') and message.media:
        media_type = type(message.media).__name__
        if 'Story' in media_type or 'story' in media_type.lower():
            return True

    return False


def process_text_with_map_links(text):
    if not text:
        return None
    map_links = extract_map_links(text)
    if not map_links:
        return None
    processed = text
    found = False
    for link in map_links:
        address = resolve_google_maps_link(link)
        if address:
            processed = processed.replace(link, f"[Локация: {address}]")
            logger.info(f"Заменена ссылка на адрес: {address}")
            found = True
    return processed if found else None


async def extract_text_from_image(telegram_client, message):
    """
    Извлекает текст из изображения с помощью OpenAI Vision API

    Args:
        telegram_client: Telegram клиент
        message: Сообщение Telegram с фото

    Returns:
        str: Распознанный текст или None при ошибке
    """
    try:
        if not message.photo:
            return None

        logger.info("Начинаем распознавание текста с изображения из сообщения %s через OpenAI Vision", message.id)

        # Скачиваем фото в память
        photo_bytes = await telegram_client.download_media(message.photo, file=BytesIO())

        if not photo_bytes:
            logger.warning("Не удалось скачать фото из сообщения %s", message.id)
            return None

        # Конвертируем в base64
        photo_bytes.seek(0)
        base64_image = base64.b64encode(photo_bytes.read()).decode('utf-8')

        # Отправляем в OpenAI Vision API (используем глобальный OpenAI client)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract all visible text from this image. Return only the text content, no descriptions."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_completion_tokens=1000
        )

        text = response.choices[0].message.content if response.choices else None

        if text and text.strip():
            logger.info("Текст успешно распознан через OpenAI Vision (длина: %d символов): %s", len(text.strip()), text.strip()[:100])
            return text.strip()
        else:
            logger.info("На изображении текст не обнаружен")
            return None

    except Exception as e:
        logger.error("Ошибка при распознавании текста с изображения через OpenAI Vision: %s", e)
        return None


async def transcribe_voice_message(telegram_client, message):
    """
    Распознаёт голосовое сообщение через OpenAI Audio Transcriptions API (gpt-4o-mini-transcribe).

    Args:
        telegram_client: Telegram клиент (Telethon)
        message: Сообщение Telegram с voice или audio

    Returns:
        str: Распознанный текст или None при ошибке
    """
    try:
        voice = getattr(message, 'voice', None) or getattr(message, 'audio', None)
        if not voice and hasattr(message, 'media'):
            media_type = type(message.media).__name__
            if 'Audio' in media_type or 'Voice' in media_type or 'Document' in media_type:
                voice = message.media
        if not voice:
            return None

        logger.info("Начинаем распознавание голосового сообщения %s", message.id)

        # Скачиваем аудио в память
        audio_buffer = BytesIO()
        await telegram_client.download_media(message, file=audio_buffer)
        audio_buffer.seek(0)

        if audio_buffer.getbuffer().nbytes == 0:
            logger.warning("Не удалось скачать голосовое сообщение %s", message.id)
            return None

        # Устанавливаем имя файла для OpenAI API
        audio_buffer.name = "voice.ogg"

        # Отправляем в OpenAI Audio Transcriptions API
        transcription = client.audio.transcriptions.create(
            model=OPENAI_TRANSCRIBE_MODEL,
            file=audio_buffer,
            language="ru",
            response_format="json",
            temperature=0,
        )

        text = transcription.text if transcription else None

        if text and text.strip():
            logger.info("Голосовое сообщение %s распознано (длина: %d): %s", message.id, len(text.strip()), text.strip()[:100])
            return text.strip()
        else:
            logger.info("Голосовое сообщение %s не содержит распознаваемой речи", message.id)
            return None

    except Exception as e:
        logger.error("Ошибка при распознавании голосового сообщения %s: %s", message.id, e)
        return None


# Хранилище истории сообщений для каждого диалога
conversations_history = {}

def _dialog_key(account_id: int, dialog_id: int) -> str:
    return f"{account_id}:{dialog_id}"

def _get_or_create_history(account_id: int, dialog_id: int) -> list:
    """Получает существующую историю или создаёт новую для диалога."""
    key = _dialog_key(account_id, dialog_id)

    # Проверяем кэш
    if key in conversations_history:
        return conversations_history[key]

    # Создаём новую историю с системным промптом
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversations_history[key] = history
    logger.info("Создана новая история для диалога %s", dialog_id)
    return history

async def chat_with_openai(account_id, dialog_id, prompt):
    for attempt in range(OPENAI_RETRY_COUNT):
        try:
            logger.info("Отправляем в ChatGPT для диалога %s: %s", dialog_id, prompt[:100])

            # Получаем историю диалога
            history = _get_or_create_history(account_id, dialog_id)

            # Добавляем новое сообщение пользователя
            history.append({"role": "user", "content": prompt})

            # Отправляем запрос в ChatGPT
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=history,
                max_completion_tokens=OPENAI_MAX_OUTPUT_TOKENS
            )

            # Извлекаем ответ
            text = resp.choices[0].message.content
            if not text or not text.strip():
                logger.warning("Пустой ответ для диалога %s", dialog_id)
                text = "Нет ответа."
            else:
                text = text.strip()

            if text == "Нет ответа." and attempt < OPENAI_RETRY_COUNT - 1:
                logger.warning("Получен 'Нет ответа.', повторяем (попытка %d/%d)", attempt + 1, OPENAI_RETRY_COUNT)
                # Убираем последнее сообщение пользователя перед повтором
                history.pop()
                await asyncio.sleep(2)
                continue

            # Добавляем ответ ассистента в историю
            history.append({"role": "assistant", "content": text})

            logger.info("Получен ответ для диалога %s: %s", dialog_id, text[:100])
            return text
        except Exception as e:
            logger.error("Ошибка в chat_with_openai для диалога %s: %s", dialog_id, e)
            # Убираем последнее сообщение пользователя при ошибке
            if history and history[-1]["role"] == "user":
                history.pop()
            return f"Ошибка: {e}"

async def reconnect_if_disconnected(client):
    if not client.client.is_connected():
        logger.warning("Соединение потеряно. Попытка переподключения...")
        try:
            await client.client.connect()
            logger.info("Подключение восстановлено.")
        except Exception as e:
            logger.error("Не удалось восстановить соединение: %s", e)
            await asyncio.sleep(10)  # Пауза перед повторной попыткой переподключения

async def process_dialogue(dialog, client, processed):
    try:
        dialog_id = dialog.id
        user_name = getattr(dialog.entity, 'first_name', None) or getattr(dialog.entity, 'username', 'Неизвестно')
        me = await client.client.get_me()
        logger.info("Начало обработки диалога с пользователем '%s'", user_name)
        
        # Обновляем системный промпт для каждого нового диалога
        prompt_updated = update_system_prompt(dialog_id, user_name)
        if prompt_updated:
            logger.info("Промпт обновлен для диалога %s с пользователем '%s'", dialog_id, user_name)
        else:
            logger.info("Промпт НЕ обновлен для диалога %s с пользователем '%s' - используем текущий", dialog_id, user_name)

        # Создаём новую историю для каждого диалога
        try:
            key = _dialog_key(me.id, dialog_id)
            if key in conversations_history:
                del conversations_history[key]
            logger.info("Очищена история для диалога %s", dialog_id)
        except Exception as e:
            logger.warning("Не удалось очистить историю для диалога %s: %s", dialog_id, e)

        # Проверка соединения перед обработкой
        await reconnect_if_disconnected(client)

        # --- Проверка первого сообщения: для НЕ-фото отправляем NON_TEXT_REPLY ---
        try:
            recent = await client.client.get_messages(dialog_id, limit=1)
            if recent:
                m0 = recent[0]
                # Проверяем, что сообщение не от нас, не текстовое и не системное
                if m0.sender_id != me.id and not m0.text and not is_system_message(m0):
                    # Если это НЕ фото и НЕ голосовое (видео, стикер и т.д.) - сразу отправляем NON_TEXT_REPLY
                    has_voice = getattr(m0, 'voice', None) or getattr(m0, 'audio', None)
                    if not m0.photo and not has_voice:
                        await client.client.send_message(dialog_id, NON_TEXT_REPLY)
                        logger.info("Ответ на не-текстовое сообщение (не фото, не голосовое) пользователю '%s'", user_name)
                    # Если это фото - ничего не делаем здесь, OCR будет в основном цикле
        except Exception as e:
            logger.error("Ошибка при проверке нетекстовых сообщений: %s", e)
        # --- Конец проверки ---

        # Отправляем приветствие, если переменная SEND_DELAYED установлена в '1'
        if SEND_DELAYED == 1:
            await client.client.send_message(
                dialog_id,
                DELAYED_MESSAGE,
                schedule=datetime.now() + timedelta(minutes=DELAY_MINUTES)
            )
            logger.info("Отправка отложенного сообщения")
        
        # Отправляем FOLLOW_UP_MESSAGE с задержкой
        follow_up_delay = INITIAL_WAIT_TIME/60 + CHATGPT_WAIT_LIMIT * CHATGPT_LIMIT/60
        await client.client.send_message(
            dialog_id,
            FOLLOW_UP_MESSAGE,
            schedule=datetime.now() + timedelta(minutes=follow_up_delay)
        )
        logger.info("Отправка отложенного FOLLOW_UP_MESSAGE через %s минут", follow_up_delay)

        # Помечаем сообщения как прочитанные сразу
        try:
            await client.client.send_read_acknowledge(dialog_id)
            logger.info("Сообщения помечены как прочитанные для пользователя '%s'", user_name)
        except Exception as e:
            logger.warning("Не удалось пометить сообщения как прочитанные: %s", e)
        
        # Ждём указанное время, чтобы получить все сообщения от клиента
        logger.info("Ожидание %d секунд перед ответом для пользователя '%s'", INITIAL_WAIT_TIME, user_name)
        await asyncio.sleep(INITIAL_WAIT_TIME)
        try:
            msgs = await client.client.get_messages(dialog_id, limit=MESSAGES_LIMIT)
        except Exception as e:
            logger.error("Ошибка получения сообщений для начальной обработки диалога с '%s': %s", user_name, e)
            msgs = []

        initial_client_msgs = []
        for m in msgs:
            if m.sender_id != me.id and not is_system_message(m):
                text_parts = []

                # Проверяем текст
                if m.text:
                    processed = process_text_with_map_links(m.text)
                    text_parts.append(processed if processed else m.text)

                # Проверяем фото (даже если есть текст)
                if m.photo:
                    ocr_text = await extract_text_from_image(client.client, m)
                    if ocr_text:
                        text_parts.append(f"[Текст с изображения]: {ocr_text}")

                # Проверяем голосовое/аудио сообщение
                if getattr(m, 'voice', None) or getattr(m, 'audio', None):
                    voice_text = await transcribe_voice_message(client.client, m)
                    if voice_text:
                        text_parts.append(f"[Голосовое сообщение]: {voice_text}")

                # Добавляем сообщение если есть контент
                if text_parts:
                    initial_client_msgs.append((m, "\n".join(text_parts)))

        if initial_client_msgs:
            initial_client_msgs.sort(key=lambda item: item[0].date)
            combined = "\n".join(item[1] for item in initial_client_msgs)
            reply = await chat_with_openai(me.id, dialog_id, combined)
            try:
                await client.client.send_message(dialog_id, reply, parse_mode="markdown")
                logger.info("Отправлено начальное сообщение пользователю '%s'", user_name)
            except Exception as e:
                logger.error("Ошибка отправки начального сообщения пользователю '%s': %s", user_name, e)
            last_msg = initial_client_msgs[-1][0]
            last_time = (last_msg.date
                         if last_msg.date.tzinfo
                         else last_msg.date.replace(tzinfo=timezone.utc))
        else:
            last_time = datetime.now(timezone.utc)

        for cycle in range(CHATGPT_LIMIT):
            logger.info("Цикл %d для пользователя '%s': ожидание %d секунд...", cycle+1, user_name, CHATGPT_WAIT_LIMIT)
            await asyncio.sleep(CHATGPT_WAIT_LIMIT)
            try:
                msgs = await client.client.get_messages(dialog_id, limit=MESSAGES_LIMIT)
            except Exception as e:
                logger.error("Ошибка получения сообщений для диалога с '%s': %s", user_name, e)
                continue

            new_msgs_with_text = []
            non_text_replied = False
            for m in msgs:
                msg_time = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
                if m.sender_id == me.id or msg_time <= last_time:
                    continue

                # Пропускаем системные сообщения (joined telegram, и т.д.)
                if is_system_message(m):
                    logger.info("Пропущено системное сообщение для пользователя '%s'", user_name)
                    continue

                text_parts = []

                # Проверяем текст
                if m.text:
                    processed = process_text_with_map_links(m.text)
                    text_parts.append(processed if processed else m.text)

                # Проверяем фото (даже если есть текст)
                if m.photo:
                    ocr_text = await extract_text_from_image(client.client, m)
                    if ocr_text:
                        text_parts.append(f"[Текст с изображения]: {ocr_text}")

                # Проверяем голосовое/аудио сообщение
                if getattr(m, 'voice', None) or getattr(m, 'audio', None):
                    voice_text = await transcribe_voice_message(client.client, m)
                    if voice_text:
                        text_parts.append(f"[Голосовое сообщение]: {voice_text}")

                # Если есть контент, добавляем сообщение
                if text_parts:
                    new_msgs_with_text.append((m, "\n".join(text_parts)))
                elif not non_text_replied:
                    # Это не текст и не удалось распознать - отправляем стандартный ответ
                    await client.client.send_message(dialog_id, NON_TEXT_REPLY)
                    logger.info("Ответ на не-текстовое сообщение в цикле пользователю '%s'", user_name)
                    last_time = msg_time
                    non_text_replied = True
                    continue

            logger.info("Для пользователя '%s' найдено %d новых текстовых сообщений/локаций", user_name, len(new_msgs_with_text))
            if new_msgs_with_text:
                new_msgs_with_text.sort(key=lambda item: item[0].date)
                combined = "\n".join(item[1] for item in new_msgs_with_text)
                reply = await chat_with_openai(me.id, dialog_id, combined)
                try:
                    await client.client.send_message(dialog_id, reply, parse_mode="markdown")
                    logger.info("Отправлено сообщение пользователю '%s'", user_name)
                except Exception as e:
                    logger.error("Ошибка отправки сообщения пользователю '%s': %s", user_name, e)
                last_msg = new_msgs_with_text[-1][0]
                last_time = (last_msg.date
                             if last_msg.date.tzinfo
                             else last_msg.date.replace(tzinfo=timezone.utc))
            else:
                logger.info("За этот период для пользователя '%s' новых текстовых сообщений не обнаружено", user_name)

        logger.info("Обработка диалога с пользователем '%s' завершена", user_name)

        # Далее код пересылки в группу (не изменялся)
        if FORWARD_ENABLED == 1:
            # --- Блок пересылки информации о пользователе в групповой чат ---
            user_username = getattr(dialog.entity, 'username', None)
            user_phone = getattr(dialog.entity, 'phone', None)

            if user_username:
                tg_username_link = f'<a href="https://t.me/{user_username}">{user_username}</a>'
            else:
                tg_username_link = 'не указан'

            if user_phone:
                clean_phone = ''.join(filter(str.isdigit, user_phone))
                tg_phone_link = f'<a href="tg://resolve?phone={clean_phone}">{user_phone}</a>'
                wp_phone_link = f'<a href="https://wa.me/{clean_phone}">{user_phone}</a>'
                phone_info = f"Tg - {tg_phone_link}\nWp - {wp_phone_link}"
            else:
                phone_info = 'не указан'

            profile_info = (
                "<b>Информация о пользователе</b>\n"
                f"<b>Username:</b> {tg_username_link}\n"
                f"<b>Phone:</b> {phone_info}"
            )

            await client.client.send_message(
                GROUP_CHAT_ID,
                profile_info,
                schedule=datetime.now(),
                parse_mode="html"
            )
            logger.info("Переслана информация о пользователе в группу %s: %s", GROUP_CHAT_ID, profile_info)

            # --- Блок пересылки сообщений в групповом чате ---
            forward_time_delta = timedelta(seconds=CHATGPT_WAIT_LIMIT * (MESSAGES_LIMIT + 1))
            cutoff_time = datetime.now(timezone.utc) - forward_time_delta

            msgs = await client.client.get_messages(dialog_id, limit=MESSAGES_LIMIT * 2)
            messages_to_forward = [
                msg for msg in msgs
                if (msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)) >= cutoff_time and msg.text
            ]

            if messages_to_forward:
                msg_ids = [msg.id for msg in messages_to_forward]
                await client.client.forward_messages(
                    GROUP_CHAT_ID,
                    msg_ids,
                    from_peer=dialog.id
                )
                logger.info("Сообщения пересланы в группу %s", GROUP_CHAT_ID)
            else:
                logger.info("Нет сообщений для пересылки в группе для диалога с '%s'", user_name)
        else:
            logger.info("Пересылка сообщений отключена параметром FORWARD_ENABLED")

    except Exception as e:
        logger.error("Ошибка в process_dialogue для диалога с '%s': %s", user_name, e)
    finally:
        processed.discard(dialog.id)

# Основной цикл: авторизация, мониторинг и обработка диалогов
async def main():
    logger.info("Приложение запущено")
    client = await authorize_client("example_tdata")
    if not client:
        logger.error("Ошибка авторизации")
        return
    processed = set()
    while True:
        try:
            dialogs = await client.client.get_dialogs(limit=DIALOGS_LIMIT, folder=0,
                                                        ignore_pinned=True, ignore_migrated=True)
            logger.info("Получено %d диалогов для мониторинга", len(dialogs))
        except Exception as e:
            logger.error("Ошибка получения диалогов: %s", e)
            await reconnect_if_disconnected(client)
            await asyncio.sleep(MONITOR_INTERVAL)
            continue
        me = await client.client.get_me()
        for dialog in dialogs:
            # Только личные диалоги с реальными пользователями
            if not isinstance(dialog.entity, User):
                logger.info("Диалог с '%s' пропущен (не личный чат)", getattr(dialog.entity, 'title', 'Неизвестно'))
                continue
            if hasattr(dialog.entity, 'bot') and dialog.entity.bot:
                logger.info("Диалог с '%s' пропущен (бот)", getattr(dialog.entity, 'first_name', 'Неизвестно'))
                continue
            if dialog.unread_count == 0:
                logger.info("Диалог с '%s' пропущен (нет непрочитанных сообщений)", getattr(dialog.entity, 'first_name', 'Неизвестно'))
                continue
            user_name = getattr(dialog.entity, 'first_name', None) or getattr(dialog.entity, 'username', 'Неизвестно')
            logger.info("Мониторинг диалога с пользователем '%s'", user_name)
            try:
                msgs = await client.client.get_messages(dialog.id, limit=CHECK_OLD_MESSAGES_LIMIT)
            except Exception as e:
                logger.error("Ошибка получения сообщений для диалога с '%s': %s", user_name, e)
                continue
            
            my_msg = next((m for m in msgs if m.sender_id == me.id), None)
            if not my_msg or (datetime.now(timezone.utc) - (my_msg.date if my_msg.date.tzinfo 
                              else my_msg.date.replace(tzinfo=timezone.utc)) > timedelta(days=REPLY_COOLDOWN_DAYS)):
                if dialog.id not in processed:
                    processed.add(dialog.id)
                    asyncio.create_task(process_dialogue(dialog, client, processed))
                    logger.info("Начата обработка диалога с пользователем '%s'", user_name)
        await asyncio.sleep(DIALOGS_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
    