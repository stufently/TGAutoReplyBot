import asyncio, logging, os, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

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
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "2560"))
OPENAI_RETRY_COUNT = int(os.getenv("OPENAI_RETRY_COUNT", "3"))
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
        logger.info("Системный промпт успешно загружен из URL: %s", PROMPT_URL)
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


# Хранилище соответствия Telegram-диалога и Conversation в OpenAI
conversations_cache = {}

def _dialog_key(account_id: int, dialog_id: int) -> str:
    return f"{account_id}:{dialog_id}"

def _get_or_create_conversation(account_id: int, dialog_id: int) -> str:
    """Получает существующий или создаёт новый conversation для диалога."""
    key = _dialog_key(account_id, dialog_id)
    
    # Проверяем кэш
    if key in conversations_cache:
        return conversations_cache[key]
    
    # Создаём новый conversation
    try:
        conv = client.conversations.create()
        conv_id = conv.id
        conversations_cache[key] = conv_id
        logger.info("Создан новый conversation %s для диалога %s", conv_id, dialog_id)
        return conv_id
    except Exception as e:
        logger.error("Не удалось создать conversation для диалога %s: %s", dialog_id, e)
        raise

async def chat_with_openai(account_id, dialog_id, prompt):
    for attempt in range(OPENAI_RETRY_COUNT):
        try:
            logger.info("Отправляем в Responses API для диалога %s: %s", dialog_id, prompt)
            conv_id = _get_or_create_conversation(account_id, dialog_id)
            
            resp = client.responses.create(
                model=OPENAI_MODEL,
                conversation=conv_id,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
                    {"role": "user",   "content": [{"type": "input_text", "text": prompt}]}
                ],
                max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
                include=["reasoning.encrypted_content"]
            )
            
            text = getattr(resp, "output_text", "") or ""
            if not text.strip():
                logger.warning("Пустой output_text для диалога %s", dialog_id)
                text = "Нет ответа."
            else:
                text = text.strip()
                
            if text == "Нет ответа." and attempt < OPENAI_RETRY_COUNT - 1:
                logger.warning("Получен 'Нет ответа.', повторяем (попытка %d/%d)", attempt + 1, OPENAI_RETRY_COUNT)
                await asyncio.sleep(2)
                continue
                
            logger.info("Получен ответ для диалога %s: %s", dialog_id, text)
            return text
        except Exception as e:
            logger.error("Ошибка в chat_with_openai для диалога %s: %s", dialog_id, e)
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

        # Создаём новый conversation для каждого диалога
        try:
            key = _dialog_key(me.id, dialog_id)
            if key in conversations_cache:
                del conversations_cache[key]
            logger.info("Очищен кэш conversation для диалога %s", dialog_id)
        except Exception as e:
            logger.warning("Не удалось очистить кэш conversation для диалога %s: %s", dialog_id, e)

        # Проверка соединения перед обработкой
        await reconnect_if_disconnected(client)

        # --- Добавлено: ответ на первое нетекстовое сообщение ---
        try:
            recent = await client.client.get_messages(dialog_id, limit=1)
            if recent:
                m0 = recent[0]
                # Проверяем, что сообщение не от нас, не текстовое и не системное
                if m0.sender_id != me.id and not m0.text and not is_system_message(m0):
                    await client.client.send_message(dialog_id, NON_TEXT_REPLY)
                    logger.info("Ответ на не-текстовое сообщение пользователю '%s'", user_name)
        except Exception as e:
            logger.error("Ошибка при проверке нетекстовых сообщений: %s", e)
        # --- Конец добавления ---

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

        # Отбираем текстовые сообщения, отправленные клиентом (исключаем системные сообщения)
        initial_client_msgs = [m for m in msgs if m.sender_id != me.id and m.text and not is_system_message(m)]
        if initial_client_msgs:
            initial_client_msgs.sort(key=lambda m: m.date)
            combined = "\n".join(m.text for m in initial_client_msgs)
            reply = await chat_with_openai(me.id, dialog_id, combined)
            try:
                await client.client.send_message(dialog_id, reply, parse_mode="html")
                logger.info("Отправлено начальное сообщение пользователю '%s'", user_name)
            except Exception as e:
                logger.error("Ошибка отправки начального сообщения пользователю '%s': %s", user_name, e)
            last_time = (initial_client_msgs[-1].date
                         if initial_client_msgs[-1].date.tzinfo
                         else initial_client_msgs[-1].date.replace(tzinfo=timezone.utc))
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

            new_text_msgs = []
            non_text_replied = False
            for m in msgs:
                msg_time = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
                if m.sender_id == me.id or msg_time <= last_time:
                    continue
                
                # Пропускаем системные сообщения (joined telegram, и т.д.)
                if is_system_message(m):
                    logger.info("Пропущено системное сообщение для пользователя '%s'", user_name)
                    continue

                if not m.text and not non_text_replied:
                    await client.client.send_message(dialog_id, NON_TEXT_REPLY)
                    logger.info("Ответ на не-текстовое сообщение в цикле пользователю '%s'", user_name)
                    last_time = msg_time
                    non_text_replied = True
                    continue

                new_text_msgs.append(m)

            logger.info("Для пользователя '%s' найдено %d новых текстовых сообщений", user_name, len(new_text_msgs))
            if new_text_msgs:
                new_text_msgs.sort(key=lambda m: m.date)
                combined = "\n".join(m.text for m in new_text_msgs)
                reply = await chat_with_openai(me.id, dialog_id, combined)
                try:
                    await client.client.send_message(dialog_id, reply, parse_mode="html")
                    logger.info("Отправлено сообщение пользователю '%s'", user_name)
                except Exception as e:
                    logger.error("Ошибка отправки сообщения пользователю '%s': %s", user_name, e)
                last_time = (new_text_msgs[-1].date
                             if new_text_msgs[-1].date.tzinfo
                             else new_text_msgs[-1].date.replace(tzinfo=timezone.utc))
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
    