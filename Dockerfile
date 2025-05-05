# Этап сборки: устанавливаем сборочные зависимости и пакеты Python
FROM python:3.12.9-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    build-essential \
    python3-dev \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Финальный этап: минимальный образ без сборочных инструментов, но с необходимыми runtime библиотеками
FROM python:3.12.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Устанавливаем необходимые системные зависимости в финальном образе
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Копируем установленные библиотеки из этапа сборки
COPY --from=builder /install /usr/local

# Копируем код приложения
COPY ./app /app

CMD ["python", "handler.py"]
