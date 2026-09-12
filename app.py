import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.request

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import parse_qsl, urlparse

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

DEPOSIT_USERNAME = os.getenv(
    "DEPOSIT_USERNAME",
    "fart2_backpack"
).lstrip("@")

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


# =========================================================
# APP / DATABASE PATH
# =========================================================

APP_DIR = os.path.dirname(os.path.abspath(__file__))

DB = os.getenv(
    "DB_PATH",
    os.path.join(APP_DIR, "fartov.db")
).strip()

print("DATABASE PATH:", DB)

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(APP_DIR, "static")
    ),
    name="static"
)

bot = Bot(BOT_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)


# =========================================================
# CASE CONFIG
# =========================================================

CASE_CONFIGS = [
    {"id": 1, "name": "Кейс №1", "stars": 25, "ton": "0.25"},
    {"id": 2, "name": "Кейс №2", "stars": 50, "ton": "0.50"},
    {"id": 3, "name": "Кейс №3", "stars": 100, "ton": "1.00"},
    {"id": 4, "name": "Кейс №4", "stars": 250, "ton": "2.50"},
]

CASE1_SPECIAL_PRIZES = [
    {
        "id": "special:bear15",
        "name": "Мишка",
        "image_url": "/static/case1-bear-clean.png",
        "gift_url": "",
        "sell_stars": 15,
        "withdrawable": True,
        "special": True,
        "fixed_chance": 15.0,
    },
    {
        "id": "special:heart15",
        "name": "Сердце",
        "image_url": "/static/case1-heart-clean.png",
        "gift_url": "",
        "sell_stars": 15,
        "withdrawable": True,
        "special": True,
        "fixed_chance": 15.0,
    },
]


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(
        DB,
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(
        os.path.dirname(DB) or ".",
        exist_ok=True
    )

    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

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
            PRIMARY KEY(user_id, currency)
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
            UNIQUE(currency, tx_ref)
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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS upgrade_attempts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            target_id TEXT NOT NULL,
            chance_percent REAL NOT NULL,
            roll_percent REAL NOT NULL,
            won INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS case_wins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            case_id INTEGER NOT NULL,
            prize_id TEXT NOT NULL,
            prize_name TEXT NOT NULL,
            prize_image TEXT,
            prize_gift_url TEXT,
            sell_stars INTEGER NOT NULL DEFAULT 0,
            withdrawable INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'owned',
            paid_currency TEXT NOT NULL,
            paid_units INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawal_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            win_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at DATETIME
        )
        """)

        conn.commit()


# =========================================================
# BALANCES
# =========================================================

CURRENCY_DECIMALS = {
    "XTR": 0,
    "TON": 9,
    "USDT": 6,
    "GRAM": 9,
}


def currency_factor(currency):
    return 10 ** CURRENCY_DECIMALS[currency]


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
            VALUES(?,?,0)
            """, (
                user_id,
                currency
            ))

        conn.commit()


def get_balance_units(user_id, currency):
    ensure_balances(user_id)

    with db() as conn:
        row = conn.execute("""
        SELECT amount_units
        FROM balances
        WHERE user_id=?
        AND currency=?
        """, (
            user_id,
            currency
        )).fetchone()

    return int(row["amount_units"]) if row else 0


def credit_balance(
    user_id,
    currency,
    amount_units,
    kind,
    reference=""
):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute("""
        INSERT OR IGNORE INTO balances(
            user_id,
            currency,
            amount_units
        )
        VALUES(?,?,0)
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
        VALUES(?,?,?,?,?)
        """, (
            user_id,
            currency,
            amount_units,
            kind,
            reference
        ))

        conn.commit()


def debit_balance(
    user_id,
    currency,
    amount_units,
    kind,
    reference=""
):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute("""
        INSERT OR IGNORE INTO balances(
            user_id,
            currency,
            amount_units
        )
        VALUES(?,?,0)
        """, (
            user_id,
            currency
        ))

        row = conn.execute("""
        SELECT amount_units
        FROM balances
        WHERE user_id=?
        AND currency=?
        """, (
            user_id,
            currency
        )).fetchone()

        current = int(
            row["amount_units"]
        ) if row else 0

        if current < amount_units:
            conn.rollback()
            return False

        conn.execute("""
        UPDATE balances
        SET amount_units = amount_units - ?
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
        VALUES(?,?,?,?,?)
        """, (
            user_id,
            currency,
            -amount_units,
            kind,
            reference
        ))

        conn.commit()
        return True


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
        result[
            row["currency"]
        ] = format_units(
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
        or abs(
            int(time.time())
            - auth_date
        ) > 86400
    ):
        raise HTTPException(
            401,
            "Expired initData"
        )

    check_string = "\n".join(
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
        VALUES(?,?,?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
        """, (
            user["id"],
            user.get("username", ""),
            user.get("first_name", "")
        ))

        conn.commit()

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
        or not parsed.path.startswith(
            "/nft/"
        )
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
# TELEGRAM API / BACKPACK
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
        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),
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
                response.read().decode(
                    "utf-8"
                )
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
    if not isinstance(
        owned,
        dict
    ):
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
        "gift_url":
            f"https://t.me/nft/{slug}",
        "image_url": "",
        "sell_stars": 0,
        "withdrawable": True,
        "special": False,
    }


async def load_backpack_catalog():
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

        for owned in result.get(
            "gifts",
            []
        ):
            item = (
                normalize_owned_gift_for_catalog(
                    owned
                )
            )

            if item:
                items.append(
                    item
                )

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
                    *(
                        enrich(item)
                        for item in items
                    )
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
# CASE HELPERS
# =========================================================

def assign_equal_chances(items):
    if not items:
        return []

    count = len(items)
    base = round(
        100.0 / count,
        4
    )
    result = []
    running = 0.0

    for index, item in enumerate(items):
        copy = dict(item)

        if index == count - 1:
            chance = round(
                100.0 - running,
                4
            )
        else:
            chance = base
            running += chance

        copy["chance_percent"] = chance
        result.append(copy)

    return result


def build_case_catalog(backpack_items):
    buckets = [[], [], [], []]

    for index, item in enumerate(
        backpack_items
    ):
        buckets[
            index % 4
        ].append(
            dict(item)
        )

    case1_regular = buckets[0]
    case1 = []

    for special in CASE1_SPECIAL_PRIZES:
        item = dict(special)
        item["chance_percent"] = float(
            item.pop("fixed_chance")
        )
        case1.append(item)

    remaining = 70.0

    if case1_regular:
        regular_share = (
            remaining
            / len(case1_regular)
        )

        running = 0.0

        for index, item in enumerate(
            case1_regular
        ):
            copy = dict(item)

            if index == len(
                case1_regular
            ) - 1:
                chance = round(
                    remaining - running,
                    4
                )
            else:
                chance = round(
                    regular_share,
                    4
                )
                running += chance

            copy["chance_percent"] = chance
            case1.append(copy)

    else:
        case1[0]["chance_percent"] = 50.0
        case1[1]["chance_percent"] = 50.0

    case2 = assign_equal_chances(
        buckets[1]
    )
    case3 = assign_equal_chances(
        buckets[2]
    )
    case4 = assign_equal_chances(
        buckets[3]
    )

    prepared = [
        case1,
        case2,
        case3,
        case4
    ]

    cases = []

    for index, config in enumerate(
        CASE_CONFIGS
    ):
        cases.append({
            **config,
            "items": prepared[index]
        })

    return cases


def get_case_from_catalog(
    cases,
    case_id
):
    for item in cases:
        if int(item["id"]) == int(
            case_id
        ):
            return item

    return None


def choose_prize(items):
    if not items:
        raise HTTPException(
            409,
            "В этом кейсе пока нет доступных призов"
        )

    weighted = []
    total = 0

    for item in items:
        chance = Decimal(
            str(
                item.get(
                    "chance_percent",
                    0
                )
            )
        )

        weight = max(
            1,
            int(
                (
                    chance
                    * Decimal("10000")
                ).quantize(
                    Decimal("1"),
                    rounding=ROUND_DOWN
                )
            )
        )

        total += weight
        weighted.append(
            (item, weight)
        )

    roll = secrets.randbelow(
        total
    )

    cursor = 0

    for item, weight in weighted:
        cursor += weight

        if roll < cursor:
            return item

    return weighted[-1][0]


async def get_cases_for_user():
    items, warning = (
        await load_backpack_catalog()
    )

    return (
        build_case_catalog(items),
        warning
    )


# =========================================================
# API MODELS
# =========================================================

class InitPayload(BaseModel):
    initData: str


class DepositPayload(BaseModel):
    initData: str
    gift_url: str


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


class CaseOpenPayload(BaseModel):
    initData: str
    case_id: int


class CaseWinPayload(BaseModel):
    initData: str
    win_id: int


# =========================================================
# WEB PAGES
# =========================================================

@app.get("/")
async def index():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "index.html"
        )
    )


@app.get("/deposit")
async def deposit_page():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "deposit.html"
        )
    )


@app.get("/inventory")
async def inventory_page():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "inventory.html"
        )
    )


@app.get("/upgrade")
async def upgrade_page():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "upgrade.html"
        )
    )


@app.get("/cases")
async def cases_page():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "cases.html"
        )
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "db_path": DB
    }


# =========================================================
# USER / INVENTORY
# =========================================================

@app.post("/api/me")
async def me(
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
            and not item.get(
                "gift_name"
            )
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

                conn.commit()

            item["gift_name"] = (
                meta["name"]
            )
            item["gift_image"] = (
                meta["image"]
            )

        gifts.append(item)

    # ==========================================
    # Добавляем выигрыши из кейсов в инвентарь
    # ==========================================

    with db() as conn:
        case_rows = conn.execute("""
        SELECT
            id,
            prize_id,
            prize_name,
            prize_image,
            prize_gift_url,
            sell_stars,
            status,
            created_at
        FROM case_wins
        WHERE user_id=?
        AND status='owned'
        ORDER BY id DESC
        """, (
            user["id"],
        )).fetchall()

    for row in case_rows:
        gifts.append({
            "id": f"case:{row['id']}",
            "case_win_id": row["id"],
            "gift_url": (
                row["prize_gift_url"]
                or ""
            ),
            "status": "approved",
            "hidden": 0,
            "gift_name": (
                row["prize_name"]
                or "Приз из кейса"
            ),
            "gift_image": (
                row["prize_image"]
                or ""
            ),
            "source": "case",
            "sell_stars": int(
                row["sell_stars"]
                or 0
            )
        })

    return {
        "user": {
            "id": user["id"],
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
            "@"
            + DEPOSIT_USERNAME,
        "deposits": gifts
    }


# =========================================================
# CASE API
# =========================================================

@app.post("/api/cases/catalog")
async def cases_catalog(
    payload: InitPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    cases, warning = (
        await get_cases_for_user()
    )

    return {
        "ok": True,
        "source":
            "@"
            + DEPOSIT_USERNAME,
        "cases": cases,
        "warning": warning
    }


@app.post("/api/cases/open")
async def cases_open(
    payload: CaseOpenPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    cases, warning = (
        await get_cases_for_user()
    )

    case = get_case_from_catalog(
        cases,
        payload.case_id
    )

    if not case:
        raise HTTPException(
            404,
            "Кейс не найден"
        )

    stars_price = int(
        case["stars"]
    )

    ton_price_units = parse_amount(
        "TON",
        case["ton"]
    )

    stars_units = get_balance_units(
        user["id"],
        "XTR"
    )

    ton_units = get_balance_units(
        user["id"],
        "TON"
    )

    paid_currency = ""
    paid_units = 0

    reference = (
        f"case:{case['id']}:"
        f"{int(time.time())}:"
        f"{secrets.token_hex(5)}"
    )

    if stars_units >= stars_price:
        paid_currency = "XTR"
        paid_units = stars_price

    elif ton_units >= ton_price_units:
        paid_currency = "TON"
        paid_units = ton_price_units

    else:
        raise HTTPException(
            402,
            "Недостаточно Stars или TON"
        )

    if not debit_balance(
        user["id"],
        paid_currency,
        paid_units,
        "case_open",
        reference
    ):
        raise HTTPException(
            409,
            "Баланс изменился. Попробуйте ещё раз."
        )

    try:
        prize = choose_prize(
            case.get("items") or []
        )

        with db() as conn:
            cursor = conn.execute("""
            INSERT INTO case_wins(
                user_id,
                case_id,
                prize_id,
                prize_name,
                prize_image,
                prize_gift_url,
                sell_stars,
                withdrawable,
                status,
                paid_currency,
                paid_units
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                user["id"],
                int(case["id"]),
                str(
                    prize.get(
                        "id",
                        ""
                    )
                ),
                str(
                    prize.get(
                        "name",
                        "Приз"
                    )
                ),
                str(
                    prize.get(
                        "image_url",
                        ""
                    )
                ),
                str(
                    prize.get(
                        "gift_url",
                        ""
                    )
                ),
                int(
                    prize.get(
                        "sell_stars",
                        0
                    ) or 0
                ),
                1 if prize.get(
                    "withdrawable",
                    True
                ) else 0,
                "owned",
                paid_currency,
                paid_units
            ))

            win_id = (
                cursor.lastrowid
            )

            conn.commit()

    except Exception:
        credit_balance(
            user["id"],
            paid_currency,
            paid_units,
            "case_refund",
            reference
        )
        raise

    return {
        "ok": True,
        "win_id": win_id,
        "case_id":
            int(case["id"]),
        "paid_currency":
            paid_currency,
        "paid_amount":
            format_units(
                paid_currency,
                paid_units
            ),
        "prize": {
            "id":
                prize.get(
                    "id",
                    ""
                ),
            "name":
                prize.get(
                    "name",
                    "Приз"
                ),
            "image_url":
                prize.get(
                    "image_url",
                    ""
                ),
            "gift_url":
                prize.get(
                    "gift_url",
                    ""
                ),
            "sell_stars":
                int(
                    prize.get(
                        "sell_stars",
                        0
                    ) or 0
                ),
            "withdrawable":
                bool(
                    prize.get(
                        "withdrawable",
                        True
                    )
                )
        },
        "balances":
            get_balances(
                user["id"]
            ),
        "warning":
            warning
    }


@app.post("/api/cases/sell")
async def cases_sell(
    payload: CaseWinPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute("""
        SELECT *
        FROM case_wins
        WHERE id=?
        AND user_id=?
        """, (
            payload.win_id,
            user["id"]
        )).fetchone()

        if not row:
            conn.rollback()
            raise HTTPException(
                404,
                "Выигрыш не найден"
            )

        if row["status"] != "owned":
            conn.rollback()
            raise HTTPException(
                409,
                "Этот приз уже обработан"
            )

        sell_stars = int(
            row["sell_stars"] or 0
        )

        if sell_stars <= 0:
            conn.rollback()
            raise HTTPException(
                400,
                "Этот приз нельзя продать за Stars"
            )

        conn.execute("""
        UPDATE case_wins
        SET
            status='sold',
            resolved_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            payload.win_id,
        ))

        conn.execute("""
        INSERT OR IGNORE INTO balances(
            user_id,
            currency,
            amount_units
        )
        VALUES(?, 'XTR', 0)
        """, (
            user["id"],
        ))

        conn.execute("""
        UPDATE balances
        SET amount_units =
            amount_units + ?
        WHERE user_id=?
        AND currency='XTR'
        """, (
            sell_stars,
            user["id"]
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
            'XTR',
            ?,
            'case_sell',
            ?
        )
        """, (
            user["id"],
            sell_stars,
            f"case_win:{payload.win_id}"
        ))

        conn.commit()

    return {
        "ok": True,
        "credited_stars":
            sell_stars,
        "balances":
            get_balances(
                user["id"]
            )
    }


@app.post("/api/cases/withdraw")
async def cases_withdraw(
    payload: CaseWinPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute("""
        SELECT *
        FROM case_wins
        WHERE id=?
        AND user_id=?
        """, (
            payload.win_id,
            user["id"]
        )).fetchone()

        if not row:
            conn.rollback()
            raise HTTPException(
                404,
                "Выигрыш не найден"
            )

        if row["status"] != "owned":
            conn.rollback()
            raise HTTPException(
                409,
                "Этот приз уже обработан"
            )

        if not int(
            row["withdrawable"] or 0
        ):
            conn.rollback()
            raise HTTPException(
                400,
                "Этот приз нельзя вывести"
            )

        try:
            cursor = conn.execute("""
            INSERT INTO withdrawal_requests(
                user_id,
                win_id,
                status
            )
            VALUES(
                ?,
                ?,
                'pending'
            )
            """, (
                user["id"],
                payload.win_id
            ))

            request_id = (
                cursor.lastrowid
            )

        except sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(
                409,
                "Заявка на вывод уже создана"
            )

        conn.execute("""
        UPDATE case_wins
        SET
            status='withdraw_pending',
            resolved_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            payload.win_id,
        ))

        conn.commit()

    if ADMIN_ID:
        try:
            username = (
                user.get("username")
                or ""
            )

            user_text = (
                f"@{username}"
                if username
                else str(
                    user["id"]
                )
            )

            gift_url = (
                row["prize_gift_url"]
                or "—"
            )

            await bot.send_message(
                ADMIN_ID,
                (
                    "🎁 НОВАЯ ЗАЯВКА НА ВЫВОД\n\n"
                    f"Заявка: #{request_id}\n"
                    f"Пользователь: {user_text}\n"
                    f"User ID: {user['id']}\n"
                    f"Приз: {row['prize_name']}\n"
                    f"Win ID: {payload.win_id}\n"
                    f"NFT: {gift_url}"
                )
            )

        except Exception as error:
            print(
                "Admin withdraw notification error:",
                error
            )

    return {
        "ok": True,
        "request_id":
            request_id,
        "status":
            "withdraw_pending",
        "message":
            (
                "Заявка на вывод отправлена. "
                "Подарок будет обработан администратором."
            )
    }


# =========================================================
# UPGRADE
# =========================================================

@app.post("/api/upgrade/inventory")
async def upgrade_inventory(
    payload: InitPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    items = []

    # ==========================================
    # 1. NFT, которые пользователь внёс сам
    # ==========================================

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
                conn.commit()

        items.append({
            "id": f"deposit:{row['id']}",
            "source_id": str(row["id"]),
            "source": "deposit",
            "name": name or "Telegram Gift",
            "image_url": image or "",
            "gift_url": row["gift_url"],
            "price_ton": 0,
            "sell_stars": 0
        })

    # ==========================================
    # 2. Подарки, выигранные в кейсах
    # ==========================================

    with db() as conn:
        wins = conn.execute("""
        SELECT
            id,
            prize_id,
            prize_name,
            prize_image,
            prize_gift_url,
            sell_stars,
            status,
            created_at
        FROM case_wins
        WHERE user_id=?
        AND status='owned'
        ORDER BY id DESC
        """, (
            user["id"],
        )).fetchall()

    for win in wins:
        items.append({
            "id": f"case:{win['id']}",
            "source_id": str(win["id"]),
            "source": "case",
            "name": (
                win["prize_name"]
                or "Приз из кейса"
            ),
            "image_url": (
                win["prize_image"]
                or ""
            ),
            "gift_url": (
                win["prize_gift_url"]
                or ""
            ),
            "price_ton": 0,
            "sell_stars": int(
                win["sell_stars"]
                or 0
            ),
            "case_win_id": win["id"]
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

    items, warning = (
        await load_backpack_catalog()
    )

    return {
        "items": items,
        "warning": warning,
        "source":
            "@"
            + DEPOSIT_USERNAME
    }


@app.post("/api/upgrade/quote")
async def upgrade_quote(
    payload: UpgradeQuotePayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    try:
        source_id = int(
            payload.source_id
        )
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
            "NFT не найден в инвентаре"
        )

    catalog, _ = (
        await load_backpack_catalog()
    )

    target = next(
        (
            item
            for item in catalog
            if str(
                item["id"]
            ) == str(
                payload.target_id
            )
        ),
        None
    )

    if not target:
        raise HTTPException(
            404,
            "Цель не найдена"
        )

    chance = 50.0

    quote_id = (
        secrets.token_hex(
            16
        )
    )

    return {
        "ok": True,
        "quote_id": quote_id,
        "chance_percent": chance
    }


@app.post("/api/upgrade/play")
async def upgrade_play(
    payload: UpgradePlayPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    try:
        source_id = int(
            payload.source_id
        )
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

    catalog, _ = (
        await load_backpack_catalog()
    )

    target = next(
        (
            item
            for item in catalog
            if str(
                item["id"]
            ) == str(
                payload.target_id
            )
        ),
        None
    )

    if not target:
        raise HTTPException(
            404,
            "Цель не найдена"
        )

    chance = 50.0

    roll = (
        secrets.randbelow(
            1_000_000
        )
        / 10_000
    )

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
            str(
                payload.target_id
            ),
            chance,
            roll,
            1 if won else 0
        ))

        conn.commit()

    return {
        "ok": True,
        "won": won,
        "success": won,
        "chance_percent":
            chance,
        "roll_percent":
            roll
    }


# =========================================================
# BALANCES / PAYMENTS
# =========================================================

@app.post("/api/balances")
async def balances(
    payload: InitPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    current_balances = (
        get_balances(
            user["id"]
        )
    )

    print(
        "BALANCE READ:",
        user["id"],
        current_balances
    )

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
            current_balances,
        "addresses": {
            "TON":
                TON_DEPOSIT_ADDRESS,
            "USDT":
                USDT_DEPOSIT_ADDRESS,
            "GRAM":
                GRAM_DEPOSIT_ADDRESS
        },
        "history": [
            {
                "currency":
                    row["currency"],
                "amount":
                    format_units(
                        row["currency"],
                        row["delta_units"]
                    ),
                "kind":
                    row["kind"],
                "reference":
                    row["reference"],
                "created_at":
                    row["created_at"]
            }
            for row in history
        ],
        "requests": [
            {
                "id":
                    row["id"],
                "currency":
                    row["currency"],
                "amount":
                    format_units(
                        row["currency"],
                        row["amount_units"]
                    ),
                "tx_ref":
                    row["tx_ref"],
                "status":
                    row["status"],
                "created_at":
                    row["created_at"]
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

    amount = int(
        payload.amount
    )

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
        description=(
            f"Пополнение баланса "
            f"на {amount} Stars"
        ),
        payload=invoice_payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=
                    f"{amount} Stars",
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

    tx_ref = (
        payload.tx_ref
        .strip()
    )

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
            VALUES(?,?,?,?)
            """, (
                user["id"],
                currency,
                amount_units,
                tx_ref
            ))

            request_id = (
                cursor.lastrowid
            )

            conn.commit()

    except sqlite3.IntegrityError:
        raise HTTPException(
            409,
            "Эта транзакция уже зарегистрирована"
        )

    return {
        "ok": True,
        "request_id":
            request_id
    }


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
            VALUES(?,?,?,?)
            """, (
                user["id"],
                gift_url,
                meta["name"],
                meta["image"]
            ))

            deposit_id = (
                cursor.lastrowid
            )

            conn.commit()

    except sqlite3.IntegrityError:
        raise HTTPException(
            409,
            "Этот подарок уже зарегистрирован"
        )

    return {
        "ok": True,
        "deposit_id":
            deposit_id,
        "gift":
            meta
    }


# =========================================================
# TELEGRAM STARS HANDLERS
# =========================================================

@router.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):
    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message=(
                "Поддерживаются только Telegram Stars"
            )
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
    payment = (
        message.successful_payment
    )

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
            print(
                "STARS PAYMENT ALREADY PROCESSED:",
                charge_id
            )
            return

        conn.execute("""
        INSERT INTO star_payments(
            telegram_charge_id,
            user_id,
            amount_stars
        )
        VALUES(?,?,?)
        """, (
            charge_id,
            message.from_user.id,
            amount
        ))

        conn.commit()

    credit_balance(
        message.from_user.id,
        "XTR",
        amount,
        "stars_payment",
        charge_id
    )

    credited_balances = (
        get_balances(
            message.from_user.id
        )
    )

    print(
        "STARS CREDITED:",
        message.from_user.id,
        amount,
        credited_balances
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
                text=
                    "🎮 OPEN MINI APP",
                web_app=
                    WebAppInfo(
                        url=
                            WEBAPP_URL
                    )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text=
                "🎁 @"
                + DEPOSIT_USERNAME,
            url=
                "https://t.me/"
                + DEPOSIT_USERNAME
        )
    ])

    text = (
        "<b>Внести NFT - @fart2_backpack</b>\n\n"
        "<b>Улучшить подарки 👇</b>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=
            InlineKeyboardMarkup(
                inline_keyboard=
                    buttons
            )
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
    asyncio.run(main())
