# TGAutoReplyBot

**Description**

TGAutoReplyBot is a Python-based Telegram bot that automatically responds to clients based on predefined rules, guides them toward making payments, and then hands the conversation over to a manager. The project uses Telethon to interact with the Telegram API and OpenAI for content generation.

---

## 🚀 Features

* Automatic replies to clients using keywords and templates
* Personalized response generation with OpenAI GPT
* Authentication support via StringSession or tdata folder
* Configurable target chats and groups for auto-replies
* Simple setup through a `.env` configuration file

---

## 📋 Requirements

* Python 3.10+
* Telegram account credentials (API\_ID, API\_HASH)
* OpenAI API key (if using GPT)
* Docker (optional, for containerized deployment)

---

## 🛠 Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/stufently/TGAutoReplyBot.git
   cd TGAutoReplyBot
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate    # Linux/Mac
   venv\Scripts\activate     # Windows
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuration

1. Copy the sample environment file and edit your values:

   ```bash
   cp .env.example .env
   ```
2. Open `.env` and set the following variables (example):

   ```dotenv
   # Session string or path to tdata folder
   TELEGRAM_SESSION="<your_session_string>"

   # OpenAI GPT settings (optional)
   OPENAI_API_KEY="sk-..."
   ASSISTANT_ID="asst_xxx"

   # Proxy settings (optional)
   PROXIES="http://user:pass@proxy.example.com:8080"

   # Comma-separated list of chat or group IDs for auto-replies
   TARGET_CHAT_ID="-1001234567890"
   TARGET_GROUPS="@group1,@group2"

   # Welcome message for new users
   GREETING_MESSAGE="Hello! How can I assist you today?"
   ```

> **Note:** `.env.example` contains all supported variables with descriptions. You can obtain `TELEGRAM_SESSION` via Telethon StringSession or a separate extractor.

---

## 🏃 Usage

To start the auto-reply bot locally, run:

```bash
python monitor.py
```

The bot will handle incoming messages and forward conversations to a manager based on your rules.

---

## 🐳 Docker Deployment

1. Run the service with Docker Compose:

   ```bash
   docker-compose up -d
   ```

   To update to a new image version:

   ```bash
   docker-compose pull
   docker-compose down
   docker-compose up -d
   ```



### Процесс обновления промта (Ассистенты)
- в [Playground](https://platform.openai.com/playground/assistants?assistant=asst_vjWizQjt06NVFYtHwS6OX3b1) нет никакого учёта изменений, всё делается в репозитории,
нужно сначала изменить промт в самом [РЕПОЗИТОРИИ](https://gitlab.9qw.ru/9qw/autootvetchikchatgpt/-/blob/main/promts/autootventchik.txt?ref_type=heads), далее во вкладке Assistants, в поле "System instructions", нужно старый промт, 
заменить на новый, предварительно выбрав ассисента, которому хотим поменять промпт.


### Примечание
для софта надо сделать отдельную tdata сессию, чтобы не могло быть запущено
две одинаковые, иначе их будет выбивать
