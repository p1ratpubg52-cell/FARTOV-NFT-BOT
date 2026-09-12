from getpass import getpass
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("Одноразовая авторизация Telegram.")
print("Все секреты вводятся только в этом терминале.")

api_id = int(input("TELEGRAM_API_ID: ").strip())
api_hash = getpass("TELEGRAM_API_HASH (ввод скрыт): ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    client.start()
    session_string = client.session.save()

print("\nГОТОВО.")
print("Скопируйте строку ниже в Railway как TELEGRAM_SESSION_STRING.")
print("Не отправляйте её в чат и не добавляйте в GitHub.\n")
print(session_string)
