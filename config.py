import os

# Telegram Bot Token от @BotFather
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Anthropic API Key из console.anthropic.com
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Модель Claude для генерации постов
# Можно переопределить в Railway переменной ANTHROPIC_MODEL
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Путь к файлу с группами (Railway сохраняет между рестартами если volume подключен)
GROUPS_FILE = os.environ.get("GROUPS_FILE", "/data/groups.json")

# Путь к файлу с доп. пользователями (добавляются через бот)
USERS_FILE = os.environ.get("USERS_FILE", "/data/users.json")

# Список Telegram user_id кто может управлять ботом
# Пример в Railway: ALLOWED_USER_IDS=123456789,987654321
# Оставь пустым — доступ для всех
_raw = os.environ.get("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = [int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()]
