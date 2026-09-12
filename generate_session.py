import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]

print("Одноразовая авторизация Telegram.")
print("Введите номер телефона аккаунта @fart2_backpack в международном формате, например +79991234567.")
print("Код подтверждения и пароль 2FA вводятся только здесь, в терминале.")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    client.start()
    session_string = client.session.save()

print("\nГОТОВО.")
print("Скопируйте значение ниже и сохраните в Railway как TELEGRAM_SESSION_STRING.")
print("НЕ отправляйте его в чат и НЕ добавляйте в GitHub.\n")
print(session_string)
