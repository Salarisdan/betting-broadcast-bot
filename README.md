# Betting Broadcast Bot

Telegram бот для рассылки постов (с APK-файлом) в несколько групп/каналов.

## Возможности

- Добавлять/удалять группы через бота
- Выбирать группы перед каждой рассылкой (мультиселект с чекбоксами)
- Загружать APK каждый раз новый
- Текст вручную или через Claude AI (/gen <тема>)
- Задавать количество отправок в каждую группу
- Планировать время: сейчас / через Nм / через Nч / HH:MM / YYYY-MM-DD HH:MM

## Деплой на Railway

1. @BotFather → /newbot → сохрани токен
2. Добавь бота в группы/каналы как администратора (право на публикацию)
3. Узнай свой Telegram ID через @userinfobot
4. Залей код на GitHub → Railway → New Project → Deploy from GitHub
5. Переменные окружения в Railway:
   TELEGRAM_BOT_TOKEN=...
   ANTHROPIC_API_KEY=...
   ALLOWED_USER_IDS=твой_id

## Хранение групп (важно!)

groups.json по умолчанию сбрасывается при рестарте Railway.
Чтобы группы сохранялись:
- Railway → сервис → Volumes → Add Volume
- Mount path: /data
- Переменная: GROUPS_FILE=/data/groups.json
