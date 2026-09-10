import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time

from urllib.parse import parse_qsl

import uvicorn

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

DEPOSIT_USERNAME = os.getenv(
    "DEPOSIT_USERNAME",
    "fart2_backpack"
).lstrip("@")

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")


DB = "fartov.db"

app = FastAPI()

bot = Bot(BOT_TOKEN)

router = Router()

dp = Dispatcher()

dp.include_router(router)


def db():

    conn = sqlite3.connect(DB)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with db() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deposits(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                gift_url TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                hidden INTEGER NOT NULL DEFAULT 0,
                admin_note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME
            )
            """
        )

        columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(deposits)"
            ).fetchall()
        ]

        if "hidden" not in columns:

            conn.execute(
                """
                ALTER TABLE deposits
                ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0
                """
            )


def validate_init_data(init_data):

    if not init_data:

        raise HTTPException(
            401,
            "Missing Telegram initData"
        )

    pairs = dict(
        parse_qsl(
            init_data,
            keep_blank_values=True
        )
    )

    received_hash = pairs.pop(
        "hash",
        None
    )

    if not received_hash:

        raise HTTPException(
            401,
            "Missing hash"
        )

    auth_date = int(
        pairs.get(
            "auth_date",
            "0"
        )
    )

    if (
        not auth_date
        or abs(
            int(time.time())
            - auth_date
        ) > 86400
    ):

        raise HTTPException(
            401,
            "Expired initData"
        )

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value
        in sorted(
            pairs.items()
        )
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash
    ):

        raise HTTPException(
            401,
            "Invalid initData"
        )

    try:

        user = json.loads(
            pairs["user"]
        )

    except Exception:

        raise HTTPException(
            401,
            "Missing user"
        )

    return user


def save_user(user):

    with db() as conn:

        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                first_name
            )

            VALUES(?,?,?)

            ON CONFLICT(user_id)

            DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (
                user["id"],
                user.get(
                    "username",
                    ""
                ),
                user.get(
                    "first_name",
                    ""
                )
            )
        )


class InitPayload(BaseModel):

    initData: str


class DepositPayload(BaseModel):

    initData: str

    gift_url: str


class HideGiftPayload(BaseModel):

    initData: str

    deposit_id: int


@app.get("/")
async def index():

    return FileResponse(
        "static/index.html"
    )


@app.get("/health")
async def health():

    return {
        "ok": True
    }


@app.post("/api/me")
async def me(
    payload: InitPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                gift_url,
                status,
                hidden,
                created_at,
                reviewed_at

            FROM deposits

            WHERE user_id=?

            ORDER BY id DESC
            """,
            (
                user["id"],
            )
        ).fetchall()

    return {

        "user": {

            "id":
            user["id"],

            "first_name":
            user.get(
                "first_name",
                ""
            ),

            "username":
            user.get(
                "username",
                ""
            )

        },

        "deposit_username":
        "@" + DEPOSIT_USERNAME,

        "deposits":
        [
            dict(row)
            for row
            in rows
        ]

    }


@app.post("/api/deposit")
async def create_deposit(
    payload: DepositPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    gift_url = (
        payload.gift_url
        .strip()
    )

    if gift_url.startswith(
        "t.me/"
    ):

        gift_url = (
            "https://"
            + gift_url
        )

    if not gift_url.startswith(
        "https://t.me/nft/"
    ):

        raise HTTPException(
            400,
            "Вставь ссылку вида https://t.me/nft/..."
        )

    try:

        with db() as conn:

            cursor = conn.execute(
                """
                INSERT INTO deposits(
                    user_id,
                    gift_url
                )

                VALUES(?,?)
                """,
                (
                    user["id"],
                    gift_url
                )
            )

            deposit_id = (
                cursor.lastrowid
            )

    except sqlite3.IntegrityError:

        raise HTTPException(
            409,
            "Этот подарок уже зарегистрирован"
        )

    if ADMIN_ID:

        try:

            await bot.send_message(
                ADMIN_ID,
                f"""
🎁 Новый депозит

ID: {deposit_id}

User:
{user["id"]}

@{user.get("username","")}

Gift:
{gift_url}

Проверь подарок у @{DEPOSIT_USERNAME}

Подтвердить:
/approve {deposit_id}

Отклонить:
/reject {deposit_id}
"""
            )

        except Exception:

            pass

    return {

        "ok": True,

        "deposit_id":
        deposit_id

    }


@app.post("/api/hide-gift")
async def hide_gift(
    payload: HideGiftPayload
):

    user = validate_init_data(
        payload.initData
    )

    with db() as conn:

        gift = conn.execute(
            """
            SELECT
                id,
                status,
                hidden

            FROM deposits

            WHERE id=?
            AND user_id=?
            """,
            (
                payload.deposit_id,
                user["id"]
            )
        ).fetchone()

        if not gift:

            raise HTTPException(
                404,
                "Подарок не найден"
            )

        if gift["status"] != "approved":

            raise HTTPException(
                400,
                "Подарок не подтвержден"
            )

        if gift["hidden"]:

            raise HTTPException(
                409,
                "Подарок уже скрыт"
            )

        conn.execute(
            """
            UPDATE deposits

            SET hidden=1

            WHERE id=?
            AND user_id=?
            """,
            (
                payload.deposit_id,
                user["id"]
            )
        )

    return {
        "ok": True
    }


@router.message(
    CommandStart()
)
async def start(
    message: Message
):

    buttons = []

    if WEBAPP_URL.startswith(
        "https://"
    ):

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🎮 OPEN MINI APP",
                    web_app=WebAppInfo(
                        url=WEBAPP_URL
                    )
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=
                "🎁 @"
                + DEPOSIT_USERNAME,

                url=
                "https://t.me/"
                + DEPOSIT_USERNAME
            )
        ]
    )

    await message.answer(
        f"""
FARTOV NFT

Collectible gifts принимаются на @{DEPOSIT_USERNAME}.
""",
        reply_markup=
        InlineKeyboardMarkup(
            inline_keyboard=
            buttons
        )
    )


def is_admin(
    message
):

    return (
        ADMIN_ID
        and
        message.from_user.id
        ==
        ADMIN_ID
    )


@router.message(
    Command("approve")
)
async def approve(
    message: Message
):

    if not is_admin(
        message
    ):

        return

    parts = (
        message.text
        .split()
    )

    if (
        len(parts) != 2
        or
        not parts[1].isdigit()
    ):

        await message.answer(
            "Формат: /approve ID"
        )

        return

    deposit_id = int(
        parts[1]
    )

    with db() as conn:

        gift = conn.execute(
            """
            SELECT
                user_id,
                gift_url,
                status

            FROM deposits

            WHERE id=?
            """,
            (
                deposit_id,
            )
        ).fetchone()

        if not gift:

            await message.answer(
                "Депозит не найден."
            )

            return

        conn.execute(
            """
            UPDATE deposits

            SET
                status='approved',
                hidden=0,
                reviewed_at=CURRENT_TIMESTAMP

            WHERE id=?
            """,
            (
                deposit_id,
            )
        )

    await message.answer(
        f"✅ Депозит {deposit_id} подтвержден"
    )

    try:

        await bot.send_message(
            gift["user_id"],
            f"""
✅ Подарок подтвержден

{gift["gift_url"]}
"""
        )

    except Exception:

        pass


@router.message(
    Command("reject")
)
async def reject(
    message: Message
):

    if not is_admin(
        message
    ):

        return

    parts = (
        message.text
        .split(
            maxsplit=2
        )
    )

    if (
        len(parts) < 2
        or
        not parts[1].isdigit()
    ):

        await message.answer(
            "Формат: /reject ID"
        )

        return

    deposit_id = int(
        parts[1]
    )

    reason = (
        parts[2]
        if len(parts) > 2
        else "Не подтверждено"
    )

    with db() as conn:

        conn.execute(
            """
            UPDATE deposits

            SET
                status='rejected',
                admin_note=?,
                reviewed_at=CURRENT_TIMESTAMP

            WHERE id=?
            """,
            (
                reason,
                deposit_id
            )
        )

    await message.answer(
        f"❌ Депозит {deposit_id} отклонен"
    )


@router.message(
    Command("restore")
)
async def restore(
    message: Message
):

    if not is_admin(
        message
    ):

        return

    parts = (
        message.text
        .split()
    )

    if (
        len(parts) != 2
        or
        not parts[1].isdigit()
    ):

        await message.answer(
            "Формат: /restore ID"
        )

        return

    gift_id = int(
        parts[1]
    )

    with db() as conn:

        conn.execute(
            """
            UPDATE deposits

            SET hidden=0

            WHERE id=?
            """,
            (
                gift_id,
            )
        )

    await message.answer(
        f"♻️ Gift #{gift_id} снова виден в профиле"
    )


async def run_bot():

    await dp.start_polling(
        bot
    )


async def run_web():

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=PORT,
            log_level="info"
        )
    )

    await server.serve()


async def main():

    init_db()

    await asyncio.gather(
        run_web(),
        run_bot()
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
