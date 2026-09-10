import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import time
import urllib.request

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import parse_qsl, urlparse

import uvicorn

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
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


TON_DEPOSIT_ADDRESS = os.getenv(
    "TON_DEPOSIT_ADDRESS",
    ""
).strip()

USDT_DEPOSIT_ADDRESS = os.getenv(
    "USDT_DEPOSIT_ADDRESS",
    TON_DEPOSIT_ADDRESS
).strip()

GRAM_DEPOSIT_ADDRESS = os.getenv(
    "GRAM_DEPOSIT_ADDRESS",
    TON_DEPOSIT_ADDRESS
).strip()


PORT = int(os.getenv("PORT", "8080"))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")


DB = "fartov.db"

app = FastAPI()

bot = Bot(BOT_TOKEN)

router = Router()

dp = Dispatcher()

dp.include_router(router)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    with db() as conn:

        conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            gift_url TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            hidden INTEGER NOT NULL DEFAULT 0,
            admin_note TEXT,
            gift_name TEXT,
            gift_image TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at DATETIME
        )
        """)

        columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(deposits)"
            ).fetchall()
        ]

        if "hidden" not in columns:
            conn.execute("""
            ALTER TABLE deposits
            ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0
            """)

        if "gift_name" not in columns:
            conn.execute("""
            ALTER TABLE deposits
            ADD COLUMN gift_name TEXT
            """)

        if "gift_image" not in columns:
            conn.execute("""
            ALTER TABLE deposits
            ADD COLUMN gift_image TEXT
            """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS balances(
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount_units INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(
                user_id,
                currency
            )
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount_units INTEGER NOT NULL,
            tx_ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at DATETIME,
            UNIQUE(
                currency,
                tx_ref
            )
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS star_payments(
            telegram_charge_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount_stars INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            delta_units INTEGER NOT NULL,
            kind TEXT NOT NULL,
            reference TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)


# =========================================================
# BALANCE HELPERS
# =========================================================

CURRENCY_DECIMALS = {
    "XTR": 0,
    "TON": 9,
    "USDT": 6,
    "GRAM": 9,
}


def currency_factor(currency):
    decimals = CURRENCY_DECIMALS[currency]
    return 10 ** decimals


def parse_amount(currency, value):

    currency = currency.upper()

    if currency not in CURRENCY_DECIMALS:
        raise HTTPException(400, "ÐÐµÐ¸Ð·Ð²ÐµÑÑÐ½Ð°Ñ Ð²Ð°Ð»ÑÑÐ°")

    try:
        amount = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise HTTPException(400, "ÐÐµÐ²ÐµÑÐ½Ð°Ñ ÑÑÐ¼Ð¼Ð°")

    if amount <= 0:
        raise HTTPException(400, "Ð¡ÑÐ¼Ð¼Ð° Ð´Ð¾Ð»Ð¶Ð½Ð° Ð±ÑÑÑ Ð±Ð¾Ð»ÑÑÐµ Ð½ÑÐ»Ñ")

    factor = Decimal(currency_factor(currency))

    units = int(
        (amount * factor).quantize(
            Decimal("1"),
            rounding=ROUND_DOWN
        )
    )

    if units <= 0:
        raise HTTPException(400, "Ð¡Ð»Ð¸ÑÐºÐ¾Ð¼ Ð¼Ð°Ð»ÐµÐ½ÑÐºÐ°Ñ ÑÑÐ¼Ð¼Ð°")

    return units


def format_units(currency, units):

    decimals = CURRENCY_DECIMALS[currency]

    if decimals == 0:
        return str(int(units))

    factor = Decimal(currency_factor(currency))
    value = Decimal(units) / factor
    text = format(value, "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"


def ensure_balances(user_id):

    with db() as conn:
        for currency in CURRENCY_DECIMALS:
            conn.execute("""
            INSERT OR IGNORE INTO balances(
                user_id,
                currency,
                amount_units
            )
            VALUES(
                ?,
                ?,
                0
            )
            """, (
                user_id,
                currency
            ))


def credit_balance(
    user_id,
    currency,
    amount_units,
    kind,
    reference=""
):

    with db() as conn:

        conn.execute("""
        INSERT OR IGNORE INTO balances(
            user_id,
            currency,
            amount_units
        )
        VALUES(
            ?,
            ?,
            0
        )
        """, (
            user_id,
            currency
        ))

        conn.execute("""
        UPDATE balances
        SET amount_units = amount_units + ?
        WHERE user_id=?
        AND currency=?
        """, (
            amount_units,
            user_id,
            currency
        ))

        conn.execute("""
        INSERT INTO ledger(
            user_id,
            currency,
            delta_units,
            kind,
            reference
        )
        VALUES(
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """, (
            user_id,
            currency,
            amount_units,
            kind,
            reference
        ))


def get_balances(user_id):

    ensure_balances(user_id)

    with db() as conn:
        rows = conn.execute("""
        SELECT
            currency,
            amount_units
        FROM balances
        WHERE user_id=?
        """, (
            user_id,
        )).fetchall()

    result = {
        currency: "0"
        for currency in CURRENCY_DECIMALS
    }

    for row in rows:
        result[row["currency"]] = format_units(
            row["currency"],
            row["amount_units"]
        )

    return result


# =========================================================
# TELEGRAM INIT DATA
# =========================================================

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
        or abs(int(time.time()) - auth_date) > 86400
    ):
        raise HTTPException(
            401,
            "Expired initData"
        )

    check_string = "\n".join(
        f"{key}={value}"
        for key, value
        in sorted(pairs.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        check_string.encode(),
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
        return json.loads(
            pairs["user"]
        )
    except Exception:
        raise HTTPException(
            401,
            "Missing user"
        )


def save_user(user):

    with db() as conn:
        conn.execute("""
        INSERT INTO users(
            user_id,
            username,
            first_name
        )
        VALUES(
            ?,
            ?,
            ?
        )
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
        """, (
            user["id"],
            user.get("username", ""),
            user.get("first_name", "")
        ))

    ensure_balances(
        user["id"]
    )


# =========================================================
# NFT META
# =========================================================

def normalize_gift_url(url):

    url = url.strip()

    if url.startswith("t.me/"):
        url = "https://" + url

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.netloc not in {
            "t.me",
            "www.t.me"
        }
        or not parsed.path.startswith("/nft/")
    ):
        raise HTTPException(
            400,
            "ÐÑÑÐ°Ð²Ñ ÑÑÑÐ»ÐºÑ Ð²Ð¸Ð´Ð° https://t.me/nft/..."
        )

    return url


def get_meta_value(page, prop):

    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(prop)}["\']',
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page,
            re.I | re.S
        )

        if match:
            return html.unescape(
                match.group(1)
            ).strip()

    return ""


def fetch_gift_meta(url):

    fallback = (
        url.rstrip("/")
        .split("/")[-1]
        or "Telegram Gift"
    )

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=8
        ) as response:
            page = response.read(
                700000
            ).decode(
                "utf-8",
                "ignore"
            )

        title = get_meta_value(
            page,
            "og:title"
        )

        image = get_meta_value(
            page,
            "og:image"
        )

        title = re.sub(
            r"\s*[ââ|-]\s*Telegram\s*$",
            "",
            title or "",
            flags=re.I
        ).strip()

        return {
            "name": title or fallback,
            "image": image or ""
        }

    except Exception:
        return {
            "name": fallback,
            "image": ""
        }


# =========================================================
# API MODELS
# =========================================================

class InitPayload(BaseModel):
    initData: str


class DepositPayload(BaseModel):
    initData: str
    gift_url: str


class HideGiftPayload(BaseModel):
    initData: str
    deposit_id: int


class StarsInvoicePayload(BaseModel):
    initData: str
    amount: int


class ManualPayPayload(BaseModel):
    initData: str
    currency: str
    amount: str
    tx_ref: str


# =========================================================
# WEB
# =========================================================

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html"
    )


@app.get("/deposit")
async def deposit_page():
    return FileResponse(
        "static/deposit.html"
    )


@app.get("/inventory")
async def inventory_page():
    return FileResponse(
        "static/inventory.html"
    )


@app.get("/health")
async def health():
    return {
        "ok": True
    }


@app.post("/api/me")
async def me(payload: InitPayload):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:
        rows = conn.execute("""
        SELECT
            id,
            gift_url,
            status,
            hidden,
            gift_name,
            gift_image
        FROM deposits
        WHERE user_id=?
        ORDER BY id DESC
        """, (
            user["id"],
        )).fetchall()

    gifts = []

    for row in rows:

        item = dict(row)

        if (
            item["status"] == "approved"
            and not item.get("gift_name")
        ):

            meta = await asyncio.to_thread(
                fetch_gift_meta,
                item["gift_url"]
            )

            with db() as conn:
                conn.execute("""
                UPDATE deposits
                SET
                    gift_name=?,
                    gift_image=?
                WHERE id=?
                """, (
                    meta["name"],
                    meta["image"],
                    item["id"]
                ))

            item["gift_name"] = meta["name"]
            item["gift_image"] = meta["image"]

        gifts.append(item)

    return {
        "user": {
            "id": user["id"],
            "first_name": user.get("first_name", ""),
            "username": user.get("username", "")
        },

        "deposit_username":
        "@" + DEPOSIT_USERNAME,

        "deposits":
        gifts
    }


@app.post("/api/balances")
async def balances(payload: InitPayload):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:

        history = conn.execute("""
        SELECT
            currency,
            delta_units,
            kind,
            reference,
            created_at
        FROM ledger
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 30
        """, (
            user["id"],
        )).fetchall()

        requests = conn.execute("""
        SELECT
            id,
            currency,
            amount_units,
            tx_ref,
            status,
            created_at
        FROM payment_requests
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 20
        """, (
            user["id"],
        )).fetchall()

    return {
        "balances":
        get_balances(
            user["id"]
        ),

        "addresses": {
            "TON": TON_DEPOSIT_ADDRESS,
            "USDT": USDT_DEPOSIT_ADDRESS,
            "GRAM": GRAM_DEPOSIT_ADDRESS
        },

        "history": [
            {
                "currency": row["currency"],
                "amount": format_units(
                    row["currency"],
                    row["delta_units"]
                ),
                "kind": row["kind"],
                "reference": row["reference"],
                "created_at": row["created_at"]
            }
            for row in history
        ],

        "requests": [
            {
                "id": row["id"],
                "currency": row["currency"],
                "amount": format_units(
                    row["currency"],
                    row["amount_units"]
                ),
                "tx_ref": row["tx_ref"],
                "status": row["status"],
                "created_at": row["created_at"]
            }
            for row in requests
        ]
    }


@app.post("/api/stars-invoice")
async def stars_invoice(
    payload: StarsInvoicePayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    amount = int(payload.amount)

    if amount < 1:
        raise HTTPException(
            400,
            "ÐÐ¸Ð½Ð¸Ð¼ÑÐ¼ 1 Star"
        )

    if amount > 10000:
        raise HTTPException(
            400,
            "ÐÐ°ÐºÑÐ¸Ð¼ÑÐ¼ 10000 Stars Ð·Ð° Ð¾Ð´Ð½Ñ Ð¾Ð¿Ð»Ð°ÑÑ"
        )

    invoice_payload = (
        f"balance:stars:"
        f"{user['id']}:"
        f"{int(time.time())}"
    )

    link = await bot.create_invoice_link(
        title="ÐÐ¾Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ðµ FARTOV2",
        description=f"ÐÐ¾Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ðµ Ð±Ð°Ð»Ð°Ð½ÑÐ° Ð½Ð° {amount} Stars",
        payload=invoice_payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{amount} Stars",
                amount=amount
            )
        ],
        provider_token=""
    )

    return {
        "ok": True,
        "invoice_url": link
    }


@app.post("/api/manual-deposit")
async def manual_deposit(
    payload: ManualPayPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    currency = (
        payload.currency
        .upper()
        .strip()
    )

    if currency not in {
        "TON",
        "USDT",
        "GRAM"
    }:
        raise HTTPException(
            400,
            "ÐÐ¾Ð¶Ð½Ð¾ Ð²Ð½ÐµÑÑÐ¸ TON, USDT Ð¸Ð»Ð¸ GRAM"
        )

    tx_ref = payload.tx_ref.strip()

    if len(tx_ref) < 6:
        raise HTTPException(
            400,
            "Ð£ÐºÐ°Ð¶Ð¸ hash Ð¸Ð»Ð¸ ÑÑÑÐ»ÐºÑ ÑÑÐ°Ð½Ð·Ð°ÐºÑÐ¸Ð¸"
        )

    amount_units = parse_amount(
        currency,
        payload.amount
    )

    try:

        with db() as conn:
            cursor = conn.execute("""
            INSERT INTO payment_requests(
                user_id,
                currency,
                amount_units,
                tx_ref
            )
            VALUES(
                ?,
                ?,
                ?,
                ?
            )
            """, (
                user["id"],
                currency,
                amount_units,
                tx_ref
            ))

            request_id = cursor.lastrowid

    except sqlite3.IntegrityError:
        raise HTTPException(
            409,
            "Ð­ÑÐ° ÑÑÐ°Ð½Ð·Ð°ÐºÑÐ¸Ñ ÑÐ¶Ðµ Ð·Ð°ÑÐµÐ³Ð¸ÑÑÑÐ¸ÑÐ¾Ð²Ð°Ð½Ð°"
        )

    if ADMIN_ID:

        try:
            await bot.send_message(
                ADMIN_ID,
                f"""
ð° ÐÐ¾Ð²Ð¾Ðµ Ð¿Ð¾Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ðµ

ID: {request_id}

User:
{user["id"]}

@{user.get("username","")}

ÐÐ°Ð»ÑÑÐ°:
{currency}

Ð¡ÑÐ¼Ð¼Ð°:
{format_units(currency, amount_units)}

TX:
{tx_ref}

ÐÐ¾ÑÐ»Ðµ ÑÑÑÐ½Ð¾Ð¹ Ð¿ÑÐ¾Ð²ÐµÑÐºÐ¸:

/approvepay {request_id}

/rejectpay {request_id}
"""
            )

        except Exception as error:
            print(
                "ADMIN PAYMENT MESSAGE ERROR:",
                repr(error)
            )

    return {
        "ok": True,
        "request_id": request_id
    }


# =========================================================
# NFT DEPOSIT
# =========================================================

@app.post("/api/deposit")
async def create_deposit(
    payload: DepositPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    gift_url = normalize_gift_url(
        payload.gift_url
    )

    meta = await asyncio.to_thread(
        fetch_gift_meta,
        gift_url
    )

    try:

        with db() as conn:
            cursor = conn.execute("""
            INSERT INTO deposits(
                user_id,
                gift_url,
                gift_name,
                gift_image
            )
            VALUES(
                ?,
                ?,
                ?,
                ?
            )
            """, (
                user["id"],
                gift_url,
                meta["name"],
                meta["image"]
            ))

            deposit_id = cursor.lastrowid

    except sqlite3.IntegrityError:
        raise HTTPException(
            409,
            "Ð­ÑÐ¾Ñ Ð¿Ð¾Ð´Ð°ÑÐ¾Ðº ÑÐ¶Ðµ Ð·Ð°ÑÐµÐ³Ð¸ÑÑÑÐ¸ÑÐ¾Ð²Ð°Ð½"
        )

    if ADMIN_ID:

        try:

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="â ÐÐ¾Ð´ÑÐ²ÐµÑÐ´Ð¸ÑÑ",
                            callback_data=f"gift_approve:{deposit_id}"
                        ),
                        InlineKeyboardButton(
                            text="â ÐÑÐºÐ»Ð¾Ð½Ð¸ÑÑ",
                            callback_data=f"gift_reject:{deposit_id}"
                        )
                    ]
                ]
            )

            await bot.send_message(
                ADMIN_ID,
                f"""
ð ÐÐ¾Ð²ÑÐ¹ NFT Ð´ÐµÐ¿Ð¾Ð·Ð¸Ñ

ID: {deposit_id}

ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ:
{meta["name"]}

User:
{user["id"]}

Username:
@{user.get("username","")}

Gift:
{gift_url}
""",
                reply_markup=keyboard
            )

        except Exception as error:
            print(
                "ADMIN GIFT MESSAGE ERROR:",
                repr(error)
            )

    return {
        "ok": True,
        "deposit_id": deposit_id,
        "gift": meta
    }


@app.post("/api/hide-gift")
async def hide_gift(
    payload: HideGiftPayload
):

    user = validate_init_data(
        payload.initData
    )

    with db() as conn:

        gift = conn.execute("""
        SELECT
            id,
            status,
            hidden
        FROM deposits
        WHERE id=?
        AND user_id=?
        """, (
            payload.deposit_id,
            user["id"]
        )).fetchone()

        if not gift:
            raise HTTPException(
                404,
                "ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½"
            )

        if gift["status"] != "approved":
            raise HTTPException(
                400,
                "ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð½Ðµ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½"
            )

        conn.execute("""
        UPDATE deposits
        SET hidden=1
        WHERE id=?
        AND user_id=?
        """, (
            payload.deposit_id,
            user["id"]
        ))

    return {
        "ok": True
    }


# =========================================================
# NFT ADMIN HELPERS
# =========================================================

def get_gift(gift_id):

    with db() as conn:
        return conn.execute("""
        SELECT
            id,
            user_id,
            gift_name,
            gift_url,
            status
        FROM deposits
        WHERE id=?
        """, (
            gift_id,
        )).fetchone()


async def approve_gift_record(gift_id):

    with db() as conn:

        gift = conn.execute("""
        SELECT
            id,
            user_id,
            gift_name,
            gift_url,
            status
        FROM deposits
        WHERE id=?
        """, (
            gift_id,
        )).fetchone()

        if not gift:
            return False, "ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½", None

        if gift["status"] != "pending":
            return (
                False,
                f"ÐÐ°ÑÐ²ÐºÐ° ÑÐ¶Ðµ Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐ°Ð½Ð°: {gift['status']}",
                gift
            )

        conn.execute("""
        UPDATE deposits
        SET
            status='approved',
            hidden=0,
            admin_note=NULL,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            gift_id,
        ))

    try:
        await bot.send_message(
            gift["user_id"],
            f"""
â ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÑÐ½

{gift["gift_name"] or "Telegram Gift"}

ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½ Ð² Ð²Ð°Ñ Ð¸Ð½Ð²ÐµÐ½ÑÐ°ÑÑ.
"""
        )
    except Exception as error:
        print(
            "USER APPROVE MESSAGE ERROR:",
            repr(error)
        )

    return True, f"â Gift #{gift_id} Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÑÐ½", gift


async def reject_gift_record(
    gift_id,
    reason="ÐÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¾ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ¾Ð¼"
):

    with db() as conn:

        gift = conn.execute("""
        SELECT
            id,
            user_id,
            gift_name,
            gift_url,
            status
        FROM deposits
        WHERE id=?
        """, (
            gift_id,
        )).fetchone()

        if not gift:
            return False, "ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½", None

        if gift["status"] != "pending":
            return (
                False,
                f"ÐÐ°ÑÐ²ÐºÐ° ÑÐ¶Ðµ Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐ°Ð½Ð°: {gift['status']}",
                gift
            )

        conn.execute("""
        UPDATE deposits
        SET
            status='rejected',
            admin_note=?,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            reason,
            gift_id
        ))

    try:
        await bot.send_message(
            gift["user_id"],
            f"""
â ÐÐ¾Ð´Ð°ÑÐ¾Ðº Ð¾ÑÐºÐ»Ð¾Ð½ÑÐ½

{gift["gift_name"] or "Telegram Gift"}

ÐÑÐ»Ð¸ ÑÑÐ¾ Ð¾ÑÐ¸Ð±ÐºÐ°, Ð¾ÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð·Ð°ÑÐ²ÐºÑ ÐµÑÑ ÑÐ°Ð· Ð¿Ð¾ÑÐ»Ðµ Ð¿ÑÐ¾Ð²ÐµÑÐºÐ¸ ÑÑÑÐ»ÐºÐ¸.
"""
        )
    except Exception as error:
        print(
            "USER REJECT MESSAGE ERROR:",
            repr(error)
        )

    return True, f"â Gift #{gift_id} Ð¾ÑÐºÐ»Ð¾Ð½ÑÐ½", gift


# =========================================================
# TELEGRAM STARS
# =========================================================

@router.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message=
            "ÐÐ¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÑÑÑÑ ÑÐ¾Ð»ÑÐºÐ¾ Telegram Stars"
        )
        return

    if not query.invoice_payload.startswith(
        "balance:stars:"
    ):
        await query.answer(
            ok=False,
            error_message=
            "ÐÐµÐ²ÐµÑÐ½ÑÐ¹ Ð¿Ð»Ð°ÑÐµÐ¶"
        )
        return

    await query.answer(
        ok=True
    )


@router.message(
    F.successful_payment
)
async def successful_payment(
    message: Message
):

    payment = message.successful_payment

    if not payment:
        return

    if payment.currency != "XTR":
        return

    if not payment.invoice_payload.startswith(
        "balance:stars:"
    ):
        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    amount = int(
        payment.total_amount
    )

    with db() as conn:

        exists = conn.execute("""
        SELECT telegram_charge_id
        FROM star_payments
        WHERE telegram_charge_id=?
        """, (
            charge_id,
        )).fetchone()

        if exists:
            return

        conn.execute("""
        INSERT INTO star_payments(
            telegram_charge_id,
            user_id,
            amount_stars
        )
        VALUES(
            ?,
            ?,
            ?
        )
        """, (
            charge_id,
            message.from_user.id,
            amount
        ))

    credit_balance(
        message.from_user.id,
        "XTR",
        amount,
        "stars_payment",
        charge_id
    )

    await message.answer(
        f"â­ ÐÐ°Ð»Ð°Ð½Ñ Ð¿Ð¾Ð¿Ð¾Ð»Ð½ÐµÐ½ Ð½Ð° {amount} Stars"
    )


# =========================================================
# BOT START
# =========================================================

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
        buttons.append([
            InlineKeyboardButton(
                text="\U0001F3AE OPEN MINI APP",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="\U0001F381 @" + DEPOSIT_USERNAME,
            url="https://t.me/" + DEPOSIT_USERNAME
        )
    ])

    text = (
        "<b>\u0412\u043d\u0435\u0441\u0442\u0438 NFT - @fart2_backpack</b>\n\n"
        "<b>\u0423\u043b\u0443\u0447\u0448\u0438\u0442\u044c "
        "\u043f\u043e\u0434\u0430\u0440\u043a\u0438 \U0001F447</b>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


def is_admin(message):
    return (
        ADMIN_ID
        and message.from_user.id == ADMIN_ID
    )


def is_admin_callback(callback):
    return (
        ADMIN_ID
        and callback.from_user.id == ADMIN_ID
    )


# =========================================================
# NFT ADMIN CALLBACK BUTTONS
# =========================================================

@router.callback_query(
    F.data.startswith("gift_approve:")
)
async def gift_approve_callback(
    callback: CallbackQuery
):

    if not is_admin_callback(callback):
        await callback.answer(
            "ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°",
            show_alert=True
        )
        return

    try:
        gift_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "ÐÐµÐ²ÐµÑÐ½ÑÐ¹ ID",
            show_alert=True
        )
        return

    ok, text, gift = await approve_gift_record(
        gift_id
    )

    await callback.answer(
        text,
        show_alert=not ok
    )

    if callback.message:

        try:
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
        except Exception:
            pass

        if ok:
            try:
                await callback.message.answer(
                    f"â ÐÐ°ÑÐ²ÐºÐ° #{gift_id} Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð°"
                )
            except Exception:
                pass


@router.callback_query(
    F.data.startswith("gift_reject:")
)
async def gift_reject_callback(
    callback: CallbackQuery
):

    if not is_admin_callback(callback):
        await callback.answer(
            "ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°",
            show_alert=True
        )
        return

    try:
        gift_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "ÐÐµÐ²ÐµÑÐ½ÑÐ¹ ID",
            show_alert=True
        )
        return

    ok, text, gift = await reject_gift_record(
        gift_id
    )

    await callback.answer(
        text,
        show_alert=not ok
    )

    if callback.message:

        try:
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
        except Exception:
            pass

        if ok:
            try:
                await callback.message.answer(
                    f"â ÐÐ°ÑÐ²ÐºÐ° #{gift_id} Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð°"
                )
            except Exception:
                pass


# =========================================================
# NFT ADMIN COMMANDS
# =========================================================

@router.message(
    Command("approve")
)
async def approve(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Ð¤Ð¾ÑÐ¼Ð°Ñ: /approve ID"
        )
        return

    gift_id = int(parts[1])

    ok, text, gift = await approve_gift_record(
        gift_id
    )

    await message.answer(text)


@router.message(
    Command("reject")
)
async def reject(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split(
        maxsplit=2
    )

    if (
        len(parts) < 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Ð¤Ð¾ÑÐ¼Ð°Ñ: /reject ID [Ð¿ÑÐ¸ÑÐ¸Ð½Ð°]"
        )
        return

    gift_id = int(parts[1])

    reason = (
        parts[2]
        if len(parts) > 2
        else "ÐÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¾ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ¾Ð¼"
    )

    ok, text, gift = await reject_gift_record(
        gift_id,
        reason
    )

    await message.answer(text)


@router.message(
    Command("restore")
)
async def restore(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Ð¤Ð¾ÑÐ¼Ð°Ñ: /restore ID"
        )
        return

    gift_id = int(parts[1])

    with db() as conn:
        conn.execute("""
        UPDATE deposits
        SET hidden=0
        WHERE id=?
        """, (
            gift_id,
        ))

    await message.answer(
        f"â»ï¸ Gift #{gift_id} Ð²Ð¾Ð·Ð²ÑÐ°ÑÑÐ½ Ð² Ð¿ÑÐ¾ÑÐ¸Ð»Ñ"
    )


# =========================================================
# MONEY ADMIN
# =========================================================

@router.message(
    Command("approvepay")
)
async def approve_pay(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Ð¤Ð¾ÑÐ¼Ð°Ñ: /approvepay ID"
        )
        return

    request_id = int(parts[1])

    with db() as conn:

        payment = conn.execute("""
        SELECT
            user_id,
            currency,
            amount_units,
            tx_ref,
            status
        FROM payment_requests
        WHERE id=?
        """, (
            request_id,
        )).fetchone()

        if not payment:
            await message.answer(
                "ÐÐ°ÑÐ²ÐºÐ° Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð°"
            )
            return

        if payment["status"] != "pending":
            await message.answer(
                "Ð­ÑÐ° Ð·Ð°ÑÐ²ÐºÐ° ÑÐ¶Ðµ Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐ°Ð½Ð°"
            )
            return

        conn.execute("""
        UPDATE payment_requests
        SET
            status='approved',
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            request_id,
        ))

    credit_balance(
        payment["user_id"],
        payment["currency"],
        payment["amount_units"],
        "manual_deposit",
        f"payment_request:{request_id}"
    )

    amount_text = format_units(
        payment["currency"],
        payment["amount_units"]
    )

    await message.answer(
        f"â ÐÐ°ÑÐ²ÐºÐ° #{request_id} Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð°\n"
        f"{amount_text} {payment['currency']}"
    )

    try:
        await bot.send_message(
            payment["user_id"],
            f"""
â ÐÐ¾Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ðµ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¾

{amount_text} {payment["currency"]}
"""
        )
    except Exception:
        pass


@router.message(
    Command("rejectpay")
)
async def reject_pay(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split(
        maxsplit=2
    )

    if (
        len(parts) < 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Ð¤Ð¾ÑÐ¼Ð°Ñ: /rejectpay ID [Ð¿ÑÐ¸ÑÐ¸Ð½Ð°]"
        )
        return

    request_id = int(parts[1])

    reason = (
        parts[2]
        if len(parts) > 2
        else "ÐÐµ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¾"
    )

    with db() as conn:

        payment = conn.execute("""
        SELECT
            user_id,
            status
        FROM payment_requests
        WHERE id=?
        """, (
            request_id,
        )).fetchone()

        if not payment:
            await message.answer(
                "ÐÐ°ÑÐ²ÐºÐ° Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð°"
            )
            return

        if payment["status"] != "pending":
            await message.answer(
                "ÐÐ°ÑÐ²ÐºÐ° ÑÐ¶Ðµ Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐ°Ð½Ð°"
            )
            return

        conn.execute("""
        UPDATE payment_requests
        SET
            status='rejected',
            admin_note=?,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            reason,
            request_id
        ))

    await message.answer(
        f"â ÐÐ°ÑÐ²ÐºÐ° #{request_id} Ð¾ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð°"
    )


# =========================================================
# RUN
# =========================================================

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
