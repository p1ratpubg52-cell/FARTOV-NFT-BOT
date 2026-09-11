import asyncio
import hashlib
import hmac
import html
import json
import os 
import secrets
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

# @fart2_backpack — обычный пользователь.
# Его числовой Telegram ID:
DEPOSIT_USER_ID = 8853704536


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
        raise HTTPException(400, "Неизвестная валюта")

    try:
        amount = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise HTTPException(400, "Неверная сумма")

    if amount <= 0:
        raise HTTPException(400, "Сумма должна быть больше нуля")

    factor = Decimal(currency_factor(currency))

    units = int(
        (amount * factor).quantize(
            Decimal("1"),
            rounding=ROUND_DOWN
        )
    )

    if units <= 0:
        raise HTTPException(400, "Слишком маленькая сумма")

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
            "Вставь ссылку вида https://t.me/nft/..."
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
            r"\s*[–—|-]\s*Telegram\s*$",
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
# TELEGRAM BOT API HELPERS FOR UPGRADE CATALOG
# =========================================================

def telegram_api_call(method, payload):

    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/"
        + method
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )
    except Exception as error:
        raise RuntimeError(
            f"Telegram API error: {error}"
        )

    if not data.get("ok"):
        raise RuntimeError(
            data.get("description")
            or "Telegram API returned an error"
        )

    return data.get("result") or {}


def normalize_owned_gift_for_catalog(owned):

    if not isinstance(owned, dict):
        return None

    if owned.get("type") != "unique":
        return None

    gift = owned.get("gift") or {}

    slug = str(
        gift.get("name") or ""
    ).strip()

    if not slug:
        return None

    base_name = str(
        gift.get("base_name")
        or "Telegram Gift"
    ).strip()

    number = gift.get("number")

    display_name = (
        f"{base_name} #{number}"
        if number is not None
        else slug
    )

    return {
        "id": slug,
        "name": display_name,
        "gift_url": f"https://t.me/nft/{slug}",
        "image_url": "",
        "price_ton": 0
    }


async def load_backpack_catalog():

    # @fart2_backpack — обычный пользователь, поэтому сразу
    # получаем подарки через getUserGifts по числовому user_id.
    try:

        result = await asyncio.to_thread(
            telegram_api_call,
            "getUserGifts",
            {
                "user_id": DEPOSIT_USER_ID,
                "exclude_unlimited": True,
                "exclude_limited_upgradable": True,
                "exclude_limited_non_upgradable": True,
                "exclude_from_blockchain": False,
                "exclude_unique": False,
                "sort_by_price": True,
                "offset": "",
                "limit": 100
            }
        )

        items = []

        for owned in result.get("gifts", []):
            item = normalize_owned_gift_for_catalog(
                owned
            )
            if item:
                items.append(item)

        # Подтягиваем название/картинку с публичной NFT-страницы.
        # Даже если мета не загрузится, сам NFT всё равно останется в каталоге.
        async def enrich(item):
            meta = await asyncio.to_thread(
                fetch_gift_meta,
                item["gift_url"]
            )
            if meta.get("name"):
                item["name"] = meta["name"]
            if meta.get("image"):
                item["image_url"] = meta["image"]
            return item

        if items:
            items = list(
                await asyncio.gather(
                    *(enrich(item) for item in items)
                )
            )

        return items, ""

    except Exception as error:
        return [], (
            "Не удалось получить NFT пользователя @"
            + DEPOSIT_USERNAME
            + " (ID "
            + str(DEPOSIT_USER_ID)
            + "). Ошибка Telegram: "
            + str(error)
        )
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


class UpgradeQuotePayload(BaseModel):
    initData: str
    source_id: str
    target_id: str


class UpgradePlayPayload(BaseModel):
    initData: str
    source_id: str
    target_id: str
    quote_id: str = ""
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


@app.get("/upgrade")
async def upgrade_page():
    return FileResponse(
        "static/upgrade.html"
    )


@app.get("/static/upgrade.html")
async def upgrade_static_page():
    return FileResponse(
        "static/upgrade.html"
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


# =========================================================
# UPGRADE API
# =========================================================

@app.post("/api/upgrade/inventory")
async def upgrade_inventory(
    payload: InitPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:

        rows = conn.execute("""
        SELECT
            id,
            gift_url,
            gift_name,
            gift_image
        FROM deposits
        WHERE user_id=?
        AND status='approved'
        AND hidden=0
        ORDER BY id DESC
        """, (
            user["id"],
        )).fetchall()

    items = []

    for row in rows:

        name = row["gift_name"]
        image = row["gift_image"]

        if not name:

            meta = await asyncio.to_thread(
                fetch_gift_meta,
                row["gift_url"]
            )

            name = meta["name"]
            image = meta["image"]

            with db() as conn:
                conn.execute("""
                UPDATE deposits
                SET
                    gift_name=?,
                    gift_image=?
                WHERE id=?
                """, (
                    name,
                    image,
                    row["id"]
                ))

        items.append({
            "id": str(row["id"]),
            "name": name or "Telegram Gift",
            "image_url": image or "",
            "gift_url": row["gift_url"],
            "price_ton": 0
        })

    return {
        "items": items
    }


@app.post("/api/upgrade/catalog")
async def upgrade_catalog(
    payload: InitPayload
):

    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    items, warning = await load_backpack_catalog()

    return {
        "items": items,
        "warning": warning,
        "source": "@" + DEPOSIT_USERNAME
    }# =========================================================
# UPGRADE QUOTE
# =========================================================

@app.post("/api/upgrade/quote")
async def upgrade_quote(
    payload: UpgradeQuotePayload
):

    user = validate_init_data(payload.initData)
    save_user(user)

    try:
        source_id = int(payload.source_id)
    except Exception:
        raise HTTPException(400, "Неверный source_id")

    with db() as conn:
        source = conn.execute("""
        SELECT id
        FROM deposits
        WHERE id=?
        AND user_id=?
        AND status='approved'
        AND hidden=0
        """, (
            source_id,
            user["id"]
        )).fetchone()

    if not source:
        raise HTTPException(
            404,
            "NFT не найден в инвентаре"
        )

    catalog, warning = await load_backpack_catalog()

    target = next(
        (
            item for item in catalog
            if str(item["id"]) == str(payload.target_id)
        ),
        None
    )

    if not target:
        raise HTTPException(
            404,
            "Цель не найдена"
        )

    # Пока тестовый шанс
    chance = 50.0

    quote_id = secrets.token_hex(16)

    return {
        "ok": True,
        "quote_id": quote_id,
        "chance_percent": chance
    }


# =========================================================
# UPGRADE PLAY — ТЕСТОВЫЙ РЕЖИМ
# =========================================================

@app.post("/api/upgrade/play")
async def upgrade_play(
    payload: UpgradePlayPayload
):

    user = validate_init_data(payload.initData)
    save_user(user)

    try:
        source_id = int(payload.source_id)
    except Exception:
        raise HTTPException(
            400,
            "Неверный source_id"
        )

    with db() as conn:
        source = conn.execute("""
        SELECT id
        FROM deposits
        WHERE id=?
        AND user_id=?
        AND status='approved'
        AND hidden=0
        """, (
            source_id,
            user["id"]
        )).fetchone()

    if not source:
        raise HTTPException(
            404,
            "NFT не найден"
        )

    catalog, warning = await load_backpack_catalog()

    target = next(
        (
            item for item in catalog
            if str(item["id"]) == str(payload.target_id)
        ),
        None
    )

    if not target:
        raise HTTPException(
            404,
            "Цель не найдена"
        )

    chance = 50.0

    roll = secrets.randbelow(1_000_000) / 10_000

    won = roll < chance

    with db() as conn:
        conn.execute("""
        INSERT INTO upgrade_attempts(
            user_id,
            source_id,
            target_id,
            chance_percent,
            roll_percent,
            won
        )
        VALUES(?,?,?,?,?,?)
        """, (
            user["id"],
            source_id,
            str(payload.target_id),
            chance,
            roll,
            1 if won else 0
        ))

    return {
        "ok": True,
        "won": won,
        "success": won,
        "chance_percent": chance,
        "roll_percent": roll
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
            "Минимум 1 Star"
        )

    if amount > 10000:
        raise HTTPException(
            400,
            "Максимум 10000 Stars за одну оплату"
        )

    invoice_payload = (
        f"balance:stars:"
        f"{user['id']}:"
        f"{int(time.time())}"
    )

    link = await bot.create_invoice_link(
        title="Пополнение FARTOV2",
        description=f"Пополнение баланса на {amount} Stars",
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
            "Можно внести TON, USDT или GRAM"
        )

    tx_ref = payload.tx_ref.strip()

    if len(tx_ref) < 6:
        raise HTTPException(
            400,
            "Укажи hash или ссылку транзакции"
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
            "Эта транзакция уже зарегистрирована"
        )

    if ADMIN_ID:

        try:
            await bot.send_message(
                ADMIN_ID,
                f"""
💰 Новое пополнение

ID: {request_id}

User:
{user["id"]}

@{user.get("username","")}

Валюта:
{currency}

Сумма:
{format_units(currency, amount_units)}

TX:
{tx_ref}

После ручной проверки:

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
            "Этот подарок уже зарегистрирован"
        )

    if ADMIN_ID:

        try:

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить",
                            callback_data=f"gift_approve:{deposit_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"gift_reject:{deposit_id}"
                        )
                    ]
                ]
            )

            await bot.send_message(
                ADMIN_ID,
                f"""
🎁 Новый NFT депозит

ID: {deposit_id}

Название:
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
                "Подарок не найден"
            )

        if gift["status"] != "approved":
            raise HTTPException(
                400,
                "Подарок не подтвержден"
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
            return False, "Подарок не найден", None

        if gift["status"] != "pending":
            return (
                False,
                f"Заявка уже обработана: {gift['status']}",
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
✅ Подарок подтверждён

{gift["gift_name"] or "Telegram Gift"}

Подарок добавлен в ваш инвентарь.
"""
        )
    except Exception as error:
        print(
            "USER APPROVE MESSAGE ERROR:",
            repr(error)
        )

    return True, f"✅ Gift #{gift_id} подтверждён", gift


async def reject_gift_record(
    gift_id,
    reason="Отклонено администратором"
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
            return False, "Подарок не найден", None

        if gift["status"] != "pending":
            return (
                False,
                f"Заявка уже обработана: {gift['status']}",
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
❌ Подарок отклонён

{gift["gift_name"] or "Telegram Gift"}

Если это ошибка, отправьте заявку ещё раз после проверки ссылки.
"""
        )
    except Exception as error:
        print(
            "USER REJECT MESSAGE ERROR:",
            repr(error)
        )

    return True, f"❌ Gift #{gift_id} отклонён", gift


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
            "Поддерживаются только Telegram Stars"
        )
        return

    if not query.invoice_payload.startswith(
        "balance:stars:"
    ):
        await query.answer(
            ok=False,
            error_message=
            "Неверный платеж"
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
        f"⭐ Баланс пополнен на {amount} Stars"
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
                text="🎮 OPEN MINI APP",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🎁 @" + DEPOSIT_USERNAME,
            url="https://t.me/" + DEPOSIT_USERNAME
        )
    ])

    text = (
        "<b>Внести NFT - @fart2_backpack</b>\n\n"
        "<b>Улучшить подарки 👇</b>"
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
            "Нет доступа",
            show_alert=True
        )
        return

    try:
        gift_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "Неверный ID",
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
                    f"✅ Заявка #{gift_id} подтверждена"
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
            "Нет доступа",
            show_alert=True
        )
        return

    try:
        gift_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "Неверный ID",
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
                    f"❌ Заявка #{gift_id} отклонена"
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
            "Формат: /approve ID"
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
            "Формат: /reject ID [причина]"
        )
        return

    gift_id = int(parts[1])

    reason = (
        parts[2]
        if len(parts) > 2
        else "Отклонено администратором"
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
            "Формат: /restore ID"
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
        f"♻️ Gift #{gift_id} возвращён в профиль"
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
            "Формат: /approvepay ID"
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
                "Заявка не найдена"
            )
            return

        if payment["status"] != "pending":
            await message.answer(
                "Эта заявка уже обработана"
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
        f"✅ Заявка #{request_id} подтверждена\n"
        f"{amount_text} {payment['currency']}"
    )

    try:
        await bot.send_message(
            payment["user_id"],
            f"""
✅ Пополнение подтверждено

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
            "Формат: /rejectpay ID [причина]"
        )
        return

    request_id = int(parts[1])

    reason = (
        parts[2]
        if len(parts) > 2
        else "Не подтверждено"
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
                "Заявка не найдена"
            )
            return

        if payment["status"] != "pending":
            await message.answer(
                "Заявка уже обработана"
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
        f"❌ Заявка #{request_id} отклонена"
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
