FROM joyzoursky/python-chromedriver:3.9

WORKDIR /app

# Копируем только requirements сначала для ускорения сборки
COPY requirements.txt /app/requirements.txt
# Устанавливаем зависимости системно (так пакеты будут доступны любому пользователю в контейнере)
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir --timeout 120 --retries 5 -r /app/requirements.txt

# Копируем проект
COPY . /app

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Установим переменные окружения
ENV PYTHONUNBUFFERED=1
# По умолчанию драйвер в этом образе доступен по /usr/bin/chromedriver
ENV CHROME_DRIVER_PATH=/usr/bin/chromedriver
# По умолчанию путь к кешу куков (можно переопределить в docker-compose или env)
ENV COOKIES_CACHE_FILE=/app/cookies_cache.json

# Увеличим /dev/shm для хрома при запуске через docker-compose (в compose задаём shm_size)
CMD ["python", "main.py"]