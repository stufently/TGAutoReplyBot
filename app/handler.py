#!/usr/bin/env python
import asyncio, logging, os, json, hashlib, re
from datetime import datetime, timedelta, timezone
from telethon.tl.types import MessageService, MessageActionContactSignUp  # Импортируем MessageService для проверки типа сообщения

import openai
from telethon.errors import FloodWaitError, AuthKeyDuplicatedError
from telethon.tl.types import User
from tdata_session_exporter import authorize_client
# Загрузка параметров из переменных окружения
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "api_key")
ASSISTANT_ID        = os.environ.get("ASSISTANT_ID", "asst_vjWizQjt06NVFYtHwS6OX3b1")
PROXIES             = os.environ.get("PROXIES", "ansible.9qw.ru:8126:admin:password")
PROXY_TYPE          = os.environ.get("PROXY_TYPE", "http")
CHECK_OLD_MESSAGES_LIMIT      = int(os.environ.get("CHECK_OLD_MESSAGES_LIMIT", 20))
MESSAGES_LIMIT      = int(os.environ.get("MESSAGES_LIMIT", 10))
MONITOR_INTERVAL    = int(os.environ.get("MONITOR_INTERVAL", 30))
DIALOGS_LIMIT       = int(os.environ.get("DIALOGS_LIMIT", 10))
DIALOGS_INTERVAL    = int(os.environ.get("DIALOGS_INTERVAL", 10))
CHATGPT_LIMIT       = int(os.environ.get("CHATGPT_LIMIT", 4))
CHATGPT_WAIT_LIMIT  = int(os.environ.get("CHATGPT_WAIT_LIMIT", 60))
SEND_DELAYED        = int(os.environ.get("SEND_DELAYED", '1'))
DELAY_MINUTES       = float(os.environ.get("DELAY_MINUTES", '60'))
FORWARD_ENABLED     = int(os.environ.get("FORWARD_ENABLED", '1'))
REPLY_COOLDOWN_DAYS     = int(os.environ.get("REPLY_COOLDOWN_DAYS", '90'))
DELAYED_MESSAGE     = os.environ.get("DELAYED_MESSAGE", "Приветствую, вы определились по заказу? Может доставку или самовывоз на сегодня?")
NON_TEXT_REPLY     = os.environ.get("NON_TEXT_REPLY", "Добрый день, напишите пожалуйста текстом, где вы находитесь и какой товар вас интересует?")
FOLLOW_UP_MESSAGE     = os.environ.get("FOLLOW_UP_MESSAGE", "Если у вас ещё остались какие-то вопросы, смело задавайте")

# Имя пользователя, которого нужно пропускать при обработке сообщений
SKIP_USERNAME = "none"

openai.api_key = OPENAI_API_KEY

# Утилита для доступа к полям словаря
class dotdict(dict):
    __getattr__ = dict.__getitem__

# Дефолты для переменных 
GROUP_CHAT_ID = -1002510370326  # ID группы для пересылки диалогов
FORWARD_WAIT_TIME = int(os.environ.get("FORWARD_WAIT_TIME", 30))  # 30 минут

# Настройка логгера (сообщения на русском)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Настройка прокси для использования с tdata-session-exporter
def setup_proxy_env():
    try:
        addr, port, username, password = PROXIES.split(",")[0].strip().split(":")
        logger.info("Загружен прокси %s:%s", addr, port)
        
        # Устанавливаем переменные окружения для tdata-session-exporter
        os.environ["PROXY_TYPE"] = PROXY_TYPE
        os.environ["PROXY_HOST"] = addr
        os.environ["PROXY_PORT"] = port
        os.environ["PROXY_USERNAME"] = username
        os.environ["PROXY_PASSWORD"] = password
        
        return True
    except Exception as e:
        logger.error("Ошибка парсинга PROXIES: %s", e)
        return False

# Путь к директории tdata (используется библиотекой tdata-session-exporter)
os.environ["TDATA_PATH"] = "tdatas/tdata/"

# GPT-интеграция с кэшированием потоков
threads_cache = {}
thread_after = {}

async def chat_with_openai(dialog_id, prompt):
    try:
        logger.info("Отправляем в ChatGPT для диалога %s: %s", dialog_id, prompt)
        if dialog_id not in threads_cache:
            threads_cache[dialog_id] = openai.beta.threads.create().id
            logger.info("Создан поток %s для диалога %s", threads_cache[dialog_id], dialog_id)
        openai.beta.threads.messages.create(thread_id=threads_cache[dialog_id],
                                              role="user", content=prompt)
        logger.info("Запрос отправлен в ChatGPT для диалога %s", dialog_id)
        run = openai.beta.threads.runs.create(thread_id=threads_cache[dialog_id],
                                               assistant_id=ASSISTANT_ID)
        while True:
            status = openai.beta.threads.runs.retrieve(
                thread_id=threads_cache[dialog_id], run_id=run.id).status
            if status in ["completed", "expired", "cancelled", "failed"]:
                logger.info("Статус выполнения для диалога %s: %s", dialog_id, status)
                break
            await asyncio.sleep(1)
        after = thread_after.get(threads_cache[dialog_id])
        msgs = openai.beta.threads.messages.list(thread_id=threads_cache[dialog_id], before=after)
        for msg in msgs.data:
            if msg.role == "assistant":
                thread_after[threads_cache[dialog_id]] = msg.id
                logger.info("Получен ответ для диалога %s: %s", dialog_id, msg.content[0].text.value)
                return msg.content[0].text.value
        logger.info("Ответ не получен для диалога %s", dialog_id)
        return "Нет ответа от ассистента."
    except FloodWaitError as e:
        logger.warning("FloodWaitError в диалоге %s: ожидание %s секунд", dialog_id, e.seconds)
        await asyncio.sleep(e.seconds)
        return "FloodWaitError"
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

        # Проверка соединения перед обработкой
        await reconnect_if_disconnected(client)

        # --- Добавлено: ответ на первое нетекстовое сообщение ---
        try:
            recent = await client.client.get_messages(dialog_id, limit=1)
            if recent:
                m0 = recent[0]
                if m0.sender_id != me.id:
                    # 1) Игнорировать служебное сообщение о регистрации контакта
                    if isinstance(m0, MessageService) and isinstance(m0.action, MessageActionContactSignUp):
                        return  # не реагируем на «user joined to Telegram»
                    # 2) Обычные нетекстовые сообщения
                    if not m0.text:
                        await client.client.send_message(dialog_id, NON_TEXT_REPLY)
                        logger.info("Ответ на не-текстовое сообщение пользователю '%s'", user_name)
                        return
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

        # Ждём несколько секунд, чтобы получить начальные сообщения от клиента
        await asyncio.sleep(5)
        try:
            msgs = await client.client.get_messages(dialog_id, limit=MESSAGES_LIMIT)
        except Exception as e:
            logger.error("Ошибка получения сообщений для начальной обработки диалога с '%s': %s", user_name, e)
            msgs = []

        # Отбираем текстовые сообщения, отправленные клиентом
        initial_client_msgs = [m for m in msgs if m.sender_id != me.id and m.text]
        if initial_client_msgs:
            initial_client_msgs.sort(key=lambda m: m.date)
            combined = "\n".join(m.text for m in initial_client_msgs)
            reply = await chat_with_openai(dialog_id, combined)
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

                # Игнорируем join-сообщения
                if isinstance(m, MessageService) and isinstance(m.action, MessageActionContactSignUp):
                    continue  # просто пропускаем
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
                reply = await chat_with_openai(dialog_id, combined)
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

        await client.client.send_message(dialog_id, FOLLOW_UP_MESSAGE)
        logger.info("Обработка диалога с пользователем '%s' завершена, отправлено FOLLOW_UP_MESSAGE", user_name)

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
    if "OPENAI_API_KEY" not in os.environ or not os.environ["OPENAI_API_KEY"] or os.environ["OPENAI_API_KEY"] == "api_key":
        logger.error("OPENAI_API_KEY не настроен в переменных окружения")
        return

    # Настраиваем прокси для tdata-session-exporter
    setup_proxy_env()
    
    try:
        # Используем новую библиотеку для авторизации
        client = await authorize_client("telegram")

        if not client:
            logger.error("Ошибка авторизации")
            return
            
        processed = set()
        while True:
            try:
                dialogs = await client.client.get_dialogs(limit=DIALOGS_LIMIT, folder=0,
                                                         ignore_pinned=True, ignore_migrated=True)
                logger.info("Получено %d диалогов для мониторинга", len(dialogs))
                
                # Обработка диалогов
                for dialog in dialogs:
                    await process_dialogue(dialog, client, processed)
                
                # Ожидание перед следующей проверкой
                await asyncio.sleep(MONITOR_INTERVAL)
                
            except Exception as e:
                logger.error("Ошибка получения диалогов: %s", e)
                await reconnect_if_disconnected(client)
                await asyncio.sleep(MONITOR_INTERVAL)
    except Exception as e:
        logger.error("Ошибка в основном цикле: %s", e)
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
            
            if user_name.lower() == SKIP_USERNAME.lower():
                logger.info("Диалог с пользователем '%s' пропущен (пользователь в списке пропуска)", user_name)
                continue
                
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
    