# TGAutoReplyBot

**Telegram Auto Reply Bot** is a self‑hosted agent that uses a regular Telegram user account to automatically reply to private messages, mimicking a human operator. By leveraging **Telethon** and **OpenTele**, it bypasses Bot API limitations and provides a seamless conversation experience powered by **GPT‑4.1 mini**.

---

## 🧠 About the Project

TGAutoReplyBot uses your existing **Telegram Desktop session (`tdata`)** to log in as a regular user (not a bot). This makes it indistinguishable from a human in chat, allowing for natural interactions.

The bot:

- Responds to private messages using **OpenAI GPT‑4.1 mini**.
- Sends delayed greetings to mimic human behavior.
- Handles multi-turn conversations via OpenAI **Assistant Threads**.
- Maintains context per chat by caching thread IDs.
- Forwards messages to a manager group (optional).
- Runs using `Telethon`, `OpenTele`, and `asyncio` for full concurrency.
- Requires no Bot API token — just your `tdata` folder from Telegram Desktop.

---

## 🚀 Quick Start (Docker Compose)

1. **Clone the repository:**

   ```bash
   git clone https://github.com/stufently/TGAutoReplyBot.git
   cd TGAutoReplyBot

2. **Prepare your .env file**:
   
   Run the following command to create a working .env file based on the provided sample:
   ```bash
   cp .env.SAMPLE .env
   ```
   ✏️ Open .env in any text editor and fill in your values:
   
    - "OPENAI_API_KEY"
   
    - "ASSISTANT_ID"
   
    - "FORWARD_ENABLED, GROUP_CHAT_ID, PROXIES (optional)"

3. **Place Telegram Desktop session**:

 - Locate your tdata folder from Telegram Desktop.

 - Copy it into the project folder as: tdatas/tdata/

📁 Final structure should be: TGAutoReplyBot/tdatas/tdata/<tdata files>

4. **Create required directories**:

   ```bash
   mkdir -p tdatas sessions
These will store Telegram data and session files (needed for persistent login).


5. **Build and run the bot locally**:
      ```bash
      docker-compose up --build -d
      ```
   This will build and run the bot in detached mode. It will pull the Docker image ghcr.io/stufently/tgautoreplybot:latest and start the bot with the necessary dependencies.

6. **Check status or logs**:
 - Status:
   ```bash
   docker-compose ps
 - Logs:
   ```bash
   docker-compose logs -f

📦 **Example docker-compose.yml**
   ```bash
   version: "3.8"
   
   services:
     tgautoreplybot:
       image: ghcr.io/stufently/tgautoreplybot:latest  # Build from local Dockerfile
       container_name: tgautoreplybot
       env_file: .env                  # Load environment variables
       volumes:
         - ./tdatas:/app/tdatas        # Telegram Desktop sessions
         - ./sessions:/app/sessions    # Persist Telethon sessions
       restart: unless-stopped         # Auto-restart on failure or reboot
