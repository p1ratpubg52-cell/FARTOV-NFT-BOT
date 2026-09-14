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
    CallbackQuery,
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

from telethon import TelegramClient, functions
from telethon.sessions import StringSession


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "8853704536"
    ) or 8853704536
)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

TELEGRAM_API_ID = int(
    os.getenv("TELEGRAM_API_ID", "0") or 0
)

TELEGRAM_API_HASH = os.getenv(
    "TELEGRAM_API_HASH",
    ""
).strip()

TELEGRAM_SESSION_STRING = os.getenv(
    "TELEGRAM_SESSION_STRING",
    ""
).strip()

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

FAILURE_TOTAL_CHANCE = 25.0
FAILURE_CARD_COUNT = 2
REGULAR_NFT_CHANCE = 5.0
MAX_REGULAR_NFTS_PER_CASE = 5

CASE1_SPECIAL_PRIZES = [
    {
        "id": "special:bear15",
        "name": "Мишка",
        "image_url": "/static/case1-bear-clean.png",
        "gift_url": "",
        "sell_stars": 15,
        "withdrawable": True,
        "special": True,
        "fixed_chance": 25.0,
    },
    {
        "id": "special:heart15",
        "name": "Сердце",
        "image_url": "/static/case1-heart-clean.png",
        "gift_url": "",
        "sell_stars": 15,
        "withdrawable": True,
        "special": True,
        "fixed_chance": 25.0,
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

        upgrade_attempt_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(upgrade_attempts)"
            ).fetchall()
        ]

        if "source_type" not in upgrade_attempt_columns:
            conn.execute("""
            ALTER TABLE upgrade_attempts
            ADD COLUMN source_type TEXT
            """)

        if "source_price_stars" not in upgrade_attempt_columns:
            conn.execute("""
            ALTER TABLE upgrade_attempts
            ADD COLUMN source_price_stars INTEGER NOT NULL DEFAULT 0
            """)

        if "target_price_stars" not in upgrade_attempt_columns:
            conn.execute("""
            ALTER TABLE upgrade_attempts
            ADD COLUMN target_price_stars INTEGER NOT NULL DEFAULT 0
            """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS upgrade_quotes(
            quote_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            source_ref TEXT NOT NULL,
            target_id TEXT NOT NULL,
            source_price_stars INTEGER NOT NULL,
            target_price_stars INTEGER NOT NULL,
            chance_percent REAL NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS upgrade_target_claims(
            target_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            upgrade_attempt_id INTEGER,
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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS case_admin_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            gift_url TEXT NOT NULL,
            gift_name TEXT NOT NULL,
            gift_image TEXT,
            chance_percent REAL NOT NULL DEFAULT 5,
            sell_stars INTEGER NOT NULL DEFAULT 0,
            withdrawable INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(case_id, gift_url)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS case_settings(
            case_id INTEGER PRIMARY KEY,
            admin_managed INTEGER NOT NULL DEFAULT 0
        )
        """)

        for case_cfg in CASE_CONFIGS:
            conn.execute(
                "INSERT OR IGNORE INTO case_settings(case_id, admin_managed) VALUES(?,0)",
                (int(case_cfg["id"]),)
            )

        conn.execute("""
        UPDATE case_settings
        SET admin_managed=1
        WHERE case_id IN (
            SELECT DISTINCT case_id
            FROM case_admin_items
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

        current = int(row["amount_units"]) if row else 0

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


def require_admin(init_data):
    user = validate_init_data(init_data)

    if int(user["id"]) != int(ADMIN_ID):
        raise HTTPException(
            403,
            "Доступ только для администратора"
        )

    save_user(user)
    return user


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
            item = normalize_owned_gift_for_catalog(
                owned
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

def assign_equal_chances(items, total_chance=100.0):
    if not items:
        return []

    count = len(items)
    base = round(
        float(total_chance) / count,
        4
    )
    result = []
    running = 0.0

    for index, item in enumerate(items):
        copy = dict(item)

        if index == count - 1:
            chance = round(
                float(total_chance) - running,
                4
            )
        else:
            chance = base
            running += chance

        copy["chance_percent"] = chance
        result.append(copy)

    return result


def build_failure_items(case_id):
    each = round(
        FAILURE_TOTAL_CHANCE / FAILURE_CARD_COUNT,
        4
    )

    failures = []

    for index in range(FAILURE_CARD_COUNT):
        failures.append({
            "id": f"failure:{case_id}:{index + 1}",
            "name": "Неудача",
            "image_url": "",
            "gift_url": "",
            "sell_stars": 0,
            "withdrawable": False,
            "is_failure": True,
            "chance_percent": each,
        })

    return failures


def load_case_admin_state():
    settings = {
        int(config["id"]): False
        for config in CASE_CONFIGS
    }

    items = {
        int(config["id"]): []
        for config in CASE_CONFIGS
    }

    with db() as conn:
        setting_rows = conn.execute("""
        SELECT case_id, admin_managed
        FROM case_settings
        """).fetchall()

        for row in setting_rows:
            case_id = int(row["case_id"])
            if case_id in settings:
                settings[case_id] = bool(row["admin_managed"])

        rows = conn.execute("""
        SELECT
            id,
            case_id,
            gift_url,
            gift_name,
            gift_image,
            chance_percent,
            sell_stars,
            withdrawable
        FROM case_admin_items
        ORDER BY case_id, id
        """).fetchall()

    for row in rows:
        case_id = int(row["case_id"])
        if case_id not in items:
            continue

        items[case_id].append({
            "id": f"admin:{row['id']}",
            "admin_item_id": int(row["id"]),
            "name": row["gift_name"] or "Telegram Gift",
            "image_url": row["gift_image"] or "",
            "gift_url": row["gift_url"] or "",
            "sell_stars": int(row["sell_stars"] or 0),
            "withdrawable": bool(row["withdrawable"]),
            "special": False,
            "chance_percent": float(row["chance_percent"] or 0),
        })

    return settings, items


def build_case_catalog(backpack_items):
    buckets = [[], [], [], []]

    for index, item in enumerate(backpack_items):
        buckets[index % 4].append(dict(item))

    admin_managed, admin_items = load_case_admin_state()
    prepared = []

    for case_id, bucket in enumerate(buckets, start=1):
        items = []

        for special in CASE1_SPECIAL_PRIZES:
            item = dict(special)
            chance = float(item.pop("fixed_chance"))
            item["chance_percent"] = chance
            items.append(item)

        items.extend(build_failure_items(case_id))

        if admin_managed.get(case_id, False):
            regular_items = admin_items.get(case_id, [])

            regular_total = round(
                sum(
                    max(0.0, float(item.get("chance_percent", 0)))
                    for item in regular_items
                ),
                4
            )

            if regular_total > 25.0001:
                raise HTTPException(
                    500,
                    f"В кейсе №{case_id} сумма шансов NFT больше 25%"
                )

            for regular in regular_items:
                items.append(dict(regular))

            remaining = max(0.0, round(25.0 - regular_total, 4))
            bonus_each = remaining / 2.0

            for item in items:
                if item.get("special"):
                    item["chance_percent"] = round(
                        float(item["chance_percent"]) + bonus_each,
                        4,
                    )

        else:
            regular_items = bucket[:MAX_REGULAR_NFTS_PER_CASE]

            for regular in regular_items:
                item = dict(regular)
                item["chance_percent"] = REGULAR_NFT_CHANCE
                items.append(item)

            missing_slots = MAX_REGULAR_NFTS_PER_CASE - len(regular_items)
            if missing_slots > 0:
                missing_chance = missing_slots * REGULAR_NFT_CHANCE
                bonus_each = missing_chance / 2.0

                for item in items:
                    if item.get("special"):
                        item["chance_percent"] = round(
                            float(item["chance_percent"]) + bonus_each,
                            4,
                        )

        prepared.append(items)

    cases = []

    for index, config in enumerate(CASE_CONFIGS):
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

        if chance <= 0:
            continue

        weight = int(
            (
                chance
                * Decimal("10000")
            ).quantize(
                Decimal("1"),
                rounding=ROUND_DOWN
            )
        )

        if weight <= 0:
            continue

        total += weight
        weighted.append(
            (item, weight)
        )

    if not weighted or total <= 0:
        raise HTTPException(
            409,
            "Для этого кейса не настроены вероятности"
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
# TELEGRAM OFFICIAL MARKET
# =========================================================

_market_client = None
_market_client_lock = asyncio.Lock()
_market_price_cache = {}
MARKET_PRICE_CACHE_TTL = 60


def gift_slug_from_url(url):
    if not url:
        return ""

    try:
        parsed = urlparse(str(url).strip())
        path = parsed.path or ""

        if "/nft/" not in path:
            return ""

        slug = path.split("/nft/", 1)[1]
        slug = slug.split("/", 1)[0].strip()

        return slug

    except Exception:
        return ""


async def get_market_client():
    global _market_client

    if (
        not TELEGRAM_API_ID
        or not TELEGRAM_API_HASH
        or not TELEGRAM_SESSION_STRING
    ):
        raise RuntimeError(
            "Telegram market session is not configured"
        )

    async with _market_client_lock:
        if _market_client is None:
            _market_client = TelegramClient(
                StringSession(
                    TELEGRAM_SESSION_STRING
                ),
                TELEGRAM_API_ID,
                TELEGRAM_API_HASH
            )

        if not _market_client.is_connected():
            await _market_client.connect()

        if not await _market_client.is_user_authorized():
            raise RuntimeError(
                "Telegram user session is not authorized"
            )

        return _market_client


def _stars_amount_value(amount):
    """
    Convert Telegram StarsAmount to a positive Decimal number of Stars.
    StarsTonAmount is intentionally ignored because it is TON, not Stars.
    """
    if amount is None:
        return None

    if isinstance(amount, dict):
        type_name = str(
            amount.get("_")
            or amount.get("type")
            or ""
        ).lower()

        if "ton" in type_name:
            return None

        if (
            "starsamount" not in type_name
            and "stars_amount" not in type_name
            and "nanos" not in amount
        ):
            return None

        whole = int(
            amount.get("amount", 0)
            or 0
        )

        nanos = int(
            amount.get("nanos", 0)
            or 0
        )

    else:
        type_name = (
            type(amount).__name__
            .lower()
        )

        if "ton" in type_name:
            return None

        # Normal Telegram StarsAmount has both amount and nanos.
        # Do not treat arbitrary objects with only "amount" as Stars.
        if not hasattr(
            amount,
            "nanos"
        ):
            return None

        whole = int(
            getattr(
                amount,
                "amount",
                0
            )
            or 0
        )

        nanos = int(
            getattr(
                amount,
                "nanos",
                0
            )
            or 0
        )

    value = (
        Decimal(whole)
        + (
            Decimal(nanos)
            / Decimal("1000000000")
        )
    )

    if value <= 0:
        return None

    return value


def extract_stars_from_resell_amount(amounts):
    """
    Telegram may expose resell_amount as a vector.
    This function also tolerates a single StarsAmount object.
    """
    if amounts is None:
        return 0

    if not isinstance(
        amounts,
        (list, tuple)
    ):
        amounts = [amounts]

    best = None

    for amount in amounts:
        value = (
            _stars_amount_value(
                amount
            )
        )

        if value is None:
            continue

        if (
            best is None
            or value < best
        ):
            best = value

    if best is None:
        return 0

    # Internal balance is integer Stars.
    return max(
        1,
        int(
            best.quantize(
                Decimal("1"),
                rounding=ROUND_DOWN
            )
        )
    )


async def get_official_market_stars(
    gift_url
):
    """
    Returns the lowest current Telegram resale price in Stars
    for collectible gifts of the same base type.

    Important:
    - TON listings are NOT converted to Stars.
    - If there is no current Stars listing, returns 0.
    """
    slug = gift_slug_from_url(
        gift_url
    )

    if not slug:
        return 0

    now = time.time()

    cached = _market_price_cache.get(
        slug
    )

    if (
        cached
        and now - cached["time"]
        < MARKET_PRICE_CACHE_TTL
    ):
        return int(
            cached["price"]
        )

    client = await get_market_client()

    unique = await client(
        functions.payments.GetUniqueStarGiftRequest(
            slug=slug
        )
    )

    unique_gift = getattr(
        unique,
        "gift",
        None
    )

    if unique_gift is None:
        return 0

    # If the exact collectible itself exposes a Stars resale amount,
    # this is valid pricing information and can be used immediately.
    exact_price = (
        extract_stars_from_resell_amount(
            getattr(
                unique_gift,
                "resell_amount",
                None
            )
        )
    )

    gift_id = int(
        getattr(
            unique_gift,
            "gift_id",
            0
        )
        or 0
    )

    market_price = 0

    if gift_id > 0:
        resale = await client(
            functions.payments.GetResaleStarGiftsRequest(
                gift_id=gift_id,
                offset="",
                limit=10,
                sort_by_price=True,
                stars_only=True
            )
        )

        gifts = getattr(
            resale,
            "gifts",
            []
        ) or []

        star_prices = []

        for item in gifts:
            price = (
                extract_stars_from_resell_amount(
                    getattr(
                        item,
                        "resell_amount",
                        None
                    )
                )
            )

            if price > 0:
                star_prices.append(
                    int(price)
                )

        if star_prices:
            market_price = min(
                star_prices
            )

    price = 0

    if (
        exact_price > 0
        and market_price > 0
    ):
        price = min(
            exact_price,
            market_price
        )

    elif market_price > 0:
        price = market_price

    elif exact_price > 0:
        price = exact_price

    if price > 0:
        _market_price_cache[
            slug
        ] = {
            "time": now,
            "price": int(price)
        }

    return int(price)


async def get_gift_price_info(
    gift_url
):
    """
    Safe pricing helper for UI/API.

    market_stars > 0 only when a real Stars-denominated Telegram
    resale price was found. No TON->Stars or fiat->Stars conversion
    is fabricated.
    """
    slug = gift_slug_from_url(
        gift_url
    )

    if not slug:
        return {
            "market_stars": 0,
            "price_available": False,
            "price_source": "unavailable",
            "slug": ""
        }

    try:
        price = await get_official_market_stars(
            gift_url
        )

        if price > 0:
            return {
                "market_stars": int(price),
                "price_available": True,
                "price_source": "telegram_market_stars",
                "slug": slug
            }

    except Exception as error:
        print(
            "MARKET PRICE ERROR:",
            gift_url,
            repr(error)
        )

    # This method is useful for diagnostics/value data, but its
    # floor/average/value fields are fiat, not Stars. We therefore
    # do not pretend they are Stars.
    try:
        client = await get_market_client()

        value_info = await client(
            functions.payments.GetUniqueStarGiftValueInfoRequest(
                slug=slug
            )
        )

        return {
            "market_stars": 0,
            "price_available": False,
            "price_source": "no_stars_listing",
            "slug": slug,
            "value_currency": str(
                getattr(
                    value_info,
                    "currency",
                    ""
                )
                or ""
            ),
            "value_amount": int(
                getattr(
                    value_info,
                    "value",
                    0
                )
                or 0
            ),
            "floor_price": int(
                getattr(
                    value_info,
                    "floor_price",
                    0
                )
                or 0
            ),
            "average_price": int(
                getattr(
                    value_info,
                    "average_price",
                    0
                )
                or 0
            ),
            "initial_sale_stars": int(
                getattr(
                    value_info,
                    "initial_sale_stars",
                    0
                )
                or 0
            )
        }

    except Exception as error:
        print(
            "VALUE INFO ERROR:",
            gift_url,
            repr(error)
        )

        return {
            "market_stars": 0,
            "price_available": False,
            "price_source": "unavailable",
            "slug": slug
        }


async def safe_market_price(
    gift_url
):
    info = await get_gift_price_info(
        gift_url
    )

    return int(
        info.get(
            "market_stars",
            0
        )
        or 0
    )


# =========================================================
# API MODELS
# =========================================================

class InitPayload(BaseModel):
    initData: str


class DepositPayload(BaseModel):
    initData: str
    gift_url: str


class DepositSellPayload(BaseModel):
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


class UpgradeCatalogPayload(BaseModel):
    initData: str
    source_id: str = ""


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


class AdminCaseItemCreatePayload(BaseModel):
    initData: str
    case_id: int
    gift_url: str
    chance_percent: float


class AdminCaseItemUpdatePayload(BaseModel):
    initData: str
    item_id: int
    chance_percent: float


class AdminCaseItemDeletePayload(BaseModel):
    initData: str
    item_id: int


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


@app.get("/admin")
async def admin_page():
    return FileResponse(
        os.path.join(
            APP_DIR,
            "static",
            "admin.html"
        )
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "db_path": DB,
        "telegram_market_api_id_configured": bool(
            TELEGRAM_API_ID
        ),
        "telegram_market_api_hash_configured": bool(
            TELEGRAM_API_HASH
        ),
        "telegram_market_session_configured": bool(
            TELEGRAM_SESSION_STRING
        )
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

        item["source"] = "deposit"
        item["deposit_id"] = int(
            item["id"]
        )

        if (
            item["status"] == "approved"
            and int(
                item.get(
                    "hidden",
                    0
                )
                or 0
            ) == 0
        ):
            price_info = (
                await get_gift_price_info(
                    item["gift_url"]
                )
            )

            item["sell_stars"] = int(
                price_info.get(
                    "market_stars",
                    0
                )
                or 0
            )

            item["market_sell_stars"] = (
                item["sell_stars"]
            )

            item["price_available"] = bool(
                price_info.get(
                    "price_available",
                    False
                )
            )

            item["price_source"] = (
                price_info.get(
                    "price_source",
                    "unavailable"
                )
            )

        else:
            item["sell_stars"] = 0
            item["market_sell_stars"] = 0
            item["price_available"] = False
            item["price_source"] = "unavailable"

        gifts.append(
            item
        )

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
        gift_url = (
            row["prize_gift_url"]
            or ""
        )

        fixed_sell_stars = int(
            row["sell_stars"]
            or 0
        )

        market_sell_stars = 0

        if (
            fixed_sell_stars <= 0
            and gift_url
        ):
            market_sell_stars = (
                await safe_market_price(
                    gift_url
                )
            )

        effective_price = (
            fixed_sell_stars
            if fixed_sell_stars > 0
            else market_sell_stars
        )

        gifts.append({
            "id": f"case:{row['id']}",
            "case_win_id": row["id"],
            "gift_url": gift_url,
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
            "sell_stars":
                effective_price,
            "market_sell_stars":
                market_sell_stars,
            "price_available":
                effective_price > 0,
            "price_source": (
                "fixed"
                if fixed_sell_stars > 0
                else (
                    "telegram_market_stars"
                    if market_sell_stars > 0
                    else "unavailable"
                )
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
# ADMIN CASE API
# =========================================================

@app.post("/api/admin/status")
async def admin_status(
    payload: InitPayload
):
    user = validate_init_data(
        payload.initData
    )

    return {
        "ok": True,
        "is_admin": int(user["id"]) == int(ADMIN_ID)
    }


@app.post("/api/admin/cases/items")
async def admin_case_items(
    payload: InitPayload
):
    require_admin(
        payload.initData
    )

    with db() as conn:
        rows = conn.execute("""
        SELECT
            id,
            case_id,
            gift_url,
            gift_name,
            gift_image,
            chance_percent,
            sell_stars,
            withdrawable,
            created_at
        FROM case_admin_items
        ORDER BY case_id, id
        """).fetchall()

    grouped = {
        str(config["id"]): []
        for config in CASE_CONFIGS
    }

    for row in rows:
        grouped[str(row["case_id"])].append({
            "id": row["id"],
            "case_id": row["case_id"],
            "gift_url": row["gift_url"],
            "gift_name": row["gift_name"],
            "gift_image": row["gift_image"] or "",
            "chance_percent": float(row["chance_percent"]),
            "sell_stars": int(row["sell_stars"] or 0),
            "withdrawable": bool(row["withdrawable"]),
            "created_at": row["created_at"]
        })

    return {
        "ok": True,
        "cases": grouped
    }


@app.post("/api/admin/cases/add")
async def admin_case_add(
    payload: AdminCaseItemCreatePayload
):
    require_admin(
        payload.initData
    )

    case_id = int(payload.case_id)

    if case_id not in {
        int(config["id"])
        for config in CASE_CONFIGS
    }:
        raise HTTPException(
            400,
            "Неверный номер кейса"
        )

    chance = round(
        float(payload.chance_percent),
        4
    )

    if chance <= 0 or chance > 25:
        raise HTTPException(
            400,
            "Шанс должен быть больше 0 и не больше 25%"
        )

    gift_url = normalize_gift_url(
        payload.gift_url
    )

    with db() as conn:
        current_total = conn.execute("""
        SELECT COALESCE(
            SUM(chance_percent),
            0
        ) AS total
        FROM case_admin_items
        WHERE case_id=?
        """, (
            case_id,
        )).fetchone()["total"]

    if float(current_total or 0) + chance > 25.0001:
        raise HTTPException(
            400,
            (
                "Суммарный шанс обычных NFT в кейсе "
                "не может быть больше 25%"
            )
        )

    meta = await asyncio.to_thread(
        fetch_gift_meta,
        gift_url
    )

    try:
        with db() as conn:
            cursor = conn.execute("""
            INSERT INTO case_admin_items(
                case_id,
                gift_url,
                gift_name,
                gift_image,
                chance_percent,
                sell_stars,
                withdrawable
            )
            VALUES(?,?,?,?,?,0,1)
            """, (
                case_id,
                gift_url,
                meta["name"] or "Telegram Gift",
                meta["image"] or "",
                chance
            ))

            item_id = cursor.lastrowid

            conn.execute("""
            INSERT INTO case_settings(case_id, admin_managed)
            VALUES(?,1)
            ON CONFLICT(case_id)
            DO UPDATE SET admin_managed=1
            """, (
                case_id,
            ))

            conn.commit()

    except sqlite3.IntegrityError:
        raise HTTPException(
            409,
            "Этот NFT уже добавлен в выбранный кейс"
        )

    return {
        "ok": True,
        "item_id": item_id,
        "gift": {
            "name": meta["name"],
            "image_url": meta["image"],
            "gift_url": gift_url
        }
    }


@app.post("/api/admin/cases/update")
async def admin_case_update(
    payload: AdminCaseItemUpdatePayload
):
    require_admin(
        payload.initData
    )

    chance = round(
        float(payload.chance_percent),
        4
    )

    if chance <= 0 or chance > 25:
        raise HTTPException(
            400,
            "Шанс должен быть больше 0 и не больше 25%"
        )

    with db() as conn:
        row = conn.execute("""
        SELECT
            id,
            case_id,
            chance_percent
        FROM case_admin_items
        WHERE id=?
        """, (
            payload.item_id,
        )).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Предмет не найден"
            )

        other_total = conn.execute("""
        SELECT COALESCE(
            SUM(chance_percent),
            0
        ) AS total
        FROM case_admin_items
        WHERE case_id=?
        AND id<>?
        """, (
            row["case_id"],
            payload.item_id
        )).fetchone()["total"]

        if float(other_total or 0) + chance > 25.0001:
            raise HTTPException(
                400,
                (
                    "Суммарный шанс обычных NFT в кейсе "
                    "не может быть больше 25%"
                )
            )

        conn.execute("""
        UPDATE case_admin_items
        SET chance_percent=?
        WHERE id=?
        """, (
            chance,
            payload.item_id
        ))

        conn.commit()

    return {
        "ok": True
    }


@app.post("/api/admin/cases/delete")
async def admin_case_delete(
    payload: AdminCaseItemDeletePayload
):
    require_admin(
        payload.initData
    )

    with db() as conn:
        cursor = conn.execute("""
        DELETE FROM case_admin_items
        WHERE id=?
        """, (
            payload.item_id,
        ))

        conn.commit()

    if cursor.rowcount <= 0:
        raise HTTPException(
            404,
            "Предмет не найден"
        )

    return {
        "ok": True
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

        win_id = None

        if not prize.get("is_failure"):
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
                    str(prize.get("id", "")),
                    str(prize.get("name", "Приз")),
                    str(prize.get("image_url", "")),
                    str(prize.get("gift_url", "")),
                    int(prize.get("sell_stars", 0) or 0),
                    1 if prize.get("withdrawable", True) else 0,
                    "owned",
                    paid_currency,
                    paid_units
                ))

                win_id = cursor.lastrowid
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
                ),
            "is_failure":
                bool(
                    prize.get(
                        "is_failure",
                        False
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
        raise HTTPException(
            404,
            "Выигрыш не найден"
        )

    if row["status"] != "owned":
        raise HTTPException(
            409,
            "Этот приз уже обработан"
        )

    fixed_sell_stars = int(
        row["sell_stars"]
        or 0
    )

    market_sell_stars = 0

    if fixed_sell_stars <= 0:
        gift_url = (
            row["prize_gift_url"]
            or ""
        )

        if not gift_url:
            raise HTTPException(
                400,
                "Для этого приза нет ссылки на Telegram NFT"
            )

        market_sell_stars = (
            await safe_market_price(
                gift_url
            )
        )

        if market_sell_stars <= 0:
            raise HTTPException(
                503,
                "Не удалось получить актуальную цену с рынка Telegram"
            )

    sell_stars = (
        fixed_sell_stars
        if fixed_sell_stars > 0
        else market_sell_stars
    )

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        current = conn.execute("""
        SELECT *
        FROM case_wins
        WHERE id=?
        AND user_id=?
        """, (
            payload.win_id,
            user["id"]
        )).fetchone()

        if not current:
            conn.rollback()
            raise HTTPException(
                404,
                "Выигрыш не найден"
            )

        if current["status"] != "owned":
            conn.rollback()
            raise HTTPException(
                409,
                "Этот приз уже обработан"
            )

        conn.execute("""
        UPDATE case_wins
        SET
            status='sold',
            sell_stars=?,
            resolved_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            sell_stars,
            payload.win_id
        ))

        if int(current["case_id"] or 0) == 0:
            conn.execute("""
            DELETE FROM upgrade_target_claims
            WHERE target_id=?
            """, (
                str(
                    current["prize_id"]
                    or ""
                ),
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
            'case_sell_market',
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
        "price_source": (
            "fixed"
            if fixed_sell_stars > 0
            else "telegram_market_floor"
        ),
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
                    f"Кому отправить: {user_text}\n"
                    f"Telegram ID: {user['id']}\n"
                    f"Приз: {row['prize_name']}\n"
                    f"Win ID: {payload.win_id}\n"
                    f"NFT: {gift_url}\n\n"
                    "Статус: ожидает ручной отправки подарка."
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

UPGRADE_HOUSE_FACTOR = Decimal("0.90")
UPGRADE_QUOTE_TTL = 90
UPGRADE_MAX_CHANCE = Decimal("90.00")


def calculate_upgrade_chance(
    source_price,
    target_price
):
    try:
        source_price = Decimal(
            str(source_price)
        )

        target_price = Decimal(
            str(target_price)
        )

    except Exception:
        raise HTTPException(
            400,
            "Ошибка расчёта стоимости"
        )

    if source_price <= 0:
        raise HTTPException(
            400,
            "Не удалось определить стоимость вашего подарка"
        )

    if target_price <= 0:
        raise HTTPException(
            400,
            "Не удалось определить стоимость цели"
        )

    if target_price <= source_price:
        raise HTTPException(
            400,
            "Цель должна быть дороже вашего подарка"
        )

    chance = (
        source_price
        / target_price
        * Decimal("100")
        * UPGRADE_HOUSE_FACTOR
    )

    if chance > UPGRADE_MAX_CHANCE:
        chance = UPGRADE_MAX_CHANCE

    if chance <= 0:
        raise HTTPException(
            400,
            "Слишком маленький шанс"
        )

    chance = chance.quantize(
        Decimal("0.01"),
        rounding=ROUND_DOWN
    )

    return float(chance)


def parse_upgrade_source_ref(
    source_ref
):
    source_ref = str(
        source_ref or ""
    ).strip()

    if not source_ref:
        raise HTTPException(
            400,
            "Не выбран подарок"
        )

    if ":" in source_ref:
        source_type, raw_id = (
            source_ref.split(
                ":",
                1
            )
        )
    else:
        source_type = "deposit"
        raw_id = source_ref

    source_type = (
        source_type
        .strip()
        .lower()
    )

    if source_type not in {
        "deposit",
        "case"
    }:
        raise HTTPException(
            400,
            "Неверный тип подарка"
        )

    try:
        source_db_id = int(
            raw_id
        )
    except Exception:
        raise HTTPException(
            400,
            "Неверный ID подарка"
        )

    if source_db_id <= 0:
        raise HTTPException(
            400,
            "Неверный ID подарка"
        )

    return (
        source_type,
        source_db_id
    )


async def load_upgrade_source(
    user_id,
    source_ref
):
    source_type, source_db_id = (
        parse_upgrade_source_ref(
            source_ref
        )
    )

    if source_type == "deposit":
        with db() as conn:
            row = conn.execute("""
            SELECT
                id,
                gift_url,
                gift_name,
                gift_image
            FROM deposits
            WHERE id=?
            AND user_id=?
            AND status='approved'
            AND hidden=0
            """, (
                source_db_id,
                user_id
            )).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Подарок больше не находится в вашем инвентаре"
            )

        gift_url = (
            row["gift_url"]
            or ""
        )

        price_info = await get_gift_price_info(
            gift_url
        )

        price = int(
            price_info.get(
                "market_stars",
                0
            )
            or 0
        )

        if price <= 0:
            raise HTTPException(
                503,
                (
                    "Для этого подарка сейчас нет доступной "
                    "рыночной цены в Telegram Stars"
                )
            )

        return {
            "type": "deposit",
            "db_id": source_db_id,
            "ref": f"deposit:{source_db_id}",
            "name": (
                row["gift_name"]
                or "Telegram Gift"
            ),
            "image_url": (
                row["gift_image"]
                or ""
            ),
            "gift_url": gift_url,
            "price_stars": int(price),
            "prize_id": ""
        }

    with db() as conn:
        row = conn.execute("""
        SELECT
            id,
            prize_id,
            prize_name,
            prize_image,
            prize_gift_url,
            sell_stars
        FROM case_wins
        WHERE id=?
        AND user_id=?
        AND status='owned'
        """, (
            source_db_id,
            user_id
        )).fetchone()

    if not row:
        raise HTTPException(
            404,
            "Подарок больше не находится в вашем инвентаре"
        )

    gift_url = (
        row["prize_gift_url"]
        or ""
    )

    price = 0

    if gift_url:
        price = await safe_market_price(
            gift_url
        )

    if price <= 0:
        price = int(
            row["sell_stars"]
            or 0
        )

    if price <= 0:
        raise HTTPException(
            503,
            "Для этого подарка пока невозможно определить стоимость"
        )

    return {
        "type": "case",
        "db_id": source_db_id,
        "ref": f"case:{source_db_id}",
        "name": (
            row["prize_name"]
            or "Telegram Gift"
        ),
        "image_url": (
            row["prize_image"]
            or ""
        ),
        "gift_url": gift_url,
        "price_stars": int(price),
        "prize_id": (
            row["prize_id"]
            or ""
        )
    }


def get_claimed_upgrade_targets():
    with db() as conn:
        rows = conn.execute("""
        SELECT target_id
        FROM upgrade_target_claims
        """).fetchall()

    return {
        str(row["target_id"])
        for row in rows
    }


async def load_upgrade_targets(
    minimum_price=0
):
    catalog, warning = (
        await load_backpack_catalog()
    )

    claimed = (
        get_claimed_upgrade_targets()
    )

    semaphore = asyncio.Semaphore(
        6
    )

    async def enrich(item):
        item = dict(item)

        target_id = str(
            item.get("id")
            or ""
        )

        if not target_id:
            return None

        if target_id in claimed:
            return None

        gift_url = (
            item.get("gift_url")
            or ""
        )

        if not gift_url:
            return None

        async with semaphore:
            price = (
                await safe_market_price(
                    gift_url
                )
            )

        if price <= 0:
            return None

        item["sell_stars"] = int(price)
        item["market_stars"] = int(price)
        item["price_stars"] = int(price)

        return item

    enriched = await asyncio.gather(
        *(
            enrich(item)
            for item in catalog
        )
    )

    items = [
        item
        for item in enriched
        if item is not None
        and int(
            item.get(
                "price_stars",
                0
            )
        ) > int(
            minimum_price or 0
        )
    ]

    items.sort(
        key=lambda item: int(
            item.get(
                "price_stars",
                0
            )
        )
    )

    return (
        items,
        warning
    )


async def find_upgrade_target(
    target_id
):
    target_id = str(
        target_id or ""
    ).strip()

    if not target_id:
        raise HTTPException(
            400,
            "Цель не выбрана"
        )

    targets, warning = (
        await load_upgrade_targets()
    )

    target = next(
        (
            item
            for item in targets
            if str(
                item.get("id")
            ) == target_id
        ),
        None
    )

    if not target:
        raise HTTPException(
            404,
            "Этот подарок больше недоступен для апгрейда"
        )

    return target, warning


@app.post("/api/upgrade/inventory")
async def upgrade_inventory(
    payload: InitPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    items = []

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
        name = (
            row["gift_name"]
            or ""
        )

        image = (
            row["gift_image"]
            or ""
        )

        if not name:
            meta = await asyncio.to_thread(
                fetch_gift_meta,
                row["gift_url"]
            )

            name = (
                meta["name"]
                or "Telegram Gift"
            )

            image = (
                meta["image"]
                or ""
            )

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

        price = (
            await safe_market_price(
                row["gift_url"]
            )
        )

        items.append({
            "id":
                f"deposit:{row['id']}",
            "source_id":
                f"deposit:{row['id']}",
            "source":
                "deposit",
            "name":
                name,
            "image_url":
                image,
            "gift_url":
                row["gift_url"],
            "price_ton":
                0,
            "sell_stars":
                int(price or 0),
            "price_stars":
                int(price or 0)
        })

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
        gift_url = (
            win["prize_gift_url"]
            or ""
        )

        market_price = 0

        if gift_url:
            market_price = (
                await safe_market_price(
                    gift_url
                )
            )

        if market_price <= 0:
            market_price = int(
                win["sell_stars"]
                or 0
            )

        items.append({
            "id":
                f"case:{win['id']}",
            "source_id":
                f"case:{win['id']}",
            "source":
                "case",
            "name": (
                win["prize_name"]
                or "Приз"
            ),
            "image_url": (
                win["prize_image"]
                or ""
            ),
            "gift_url":
                gift_url,
            "price_ton":
                0,
            "sell_stars":
                int(
                    market_price
                    or 0
                ),
            "price_stars":
                int(
                    market_price
                    or 0
                ),
            "case_win_id":
                win["id"]
        })

    return {
        "items": items
    }


@app.post("/api/upgrade/catalog")
async def upgrade_catalog(
    payload: UpgradeCatalogPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    source_price = 0

    if (
        payload.source_id
        and payload.source_id.strip()
    ):
        source = (
            await load_upgrade_source(
                user["id"],
                payload.source_id
            )
        )

        source_price = int(
            source[
                "price_stars"
            ]
        )

    items, warning = (
        await load_upgrade_targets(
            minimum_price=
                source_price
        )
    )

    return {
        "items": items,
        "warning": warning,
        "source":
            "@"
            + DEPOSIT_USERNAME,
        "source_price_stars":
            source_price
    }


@app.post("/api/upgrade/quote")
async def upgrade_quote(
    payload: UpgradeQuotePayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    source = await load_upgrade_source(
        user["id"],
        payload.source_id
    )

    target, warning = (
        await find_upgrade_target(
            payload.target_id
        )
    )

    source_price = int(
        source[
            "price_stars"
        ]
    )

    target_price = int(
        target[
            "price_stars"
        ]
    )

    chance = (
        calculate_upgrade_chance(
            source_price,
            target_price
        )
    )

    quote_id = (
        secrets.token_hex(
            24
        )
    )

    expires_at = (
        int(time.time())
        + UPGRADE_QUOTE_TTL
    )

    with db() as conn:
        conn.execute("""
        DELETE FROM upgrade_quotes
        WHERE expires_at < ?
        OR used=1
        """, (
            int(time.time())
            - 300,
        ))

        conn.execute("""
        INSERT INTO upgrade_quotes(
            quote_id,
            user_id,
            source_ref,
            target_id,
            source_price_stars,
            target_price_stars,
            chance_percent,
            expires_at,
            used
        )
        VALUES(?,?,?,?,?,?,?,?,0)
        """, (
            quote_id,
            user["id"],
            source["ref"],
            str(
                target["id"]
            ),
            source_price,
            target_price,
            chance,
            expires_at
        ))

        conn.commit()

    return {
        "ok":
            True,
        "quote_id":
            quote_id,
        "chance_percent":
            chance,
        "source_price_stars":
            source_price,
        "target_price_stars":
            target_price,
        "source": {
            "id":
                source["ref"],
            "name":
                source["name"],
            "image_url":
                source["image_url"],
            "gift_url":
                source["gift_url"],
            "price_stars":
                source_price
        },
        "target": {
            "id":
                target["id"],
            "name":
                target["name"],
            "image_url":
                target["image_url"],
            "gift_url":
                target["gift_url"],
            "price_stars":
                target_price
        },
        "expires_at":
            expires_at,
        "warning":
            warning
    }


@app.post("/api/upgrade/play")
async def upgrade_play(
    payload: UpgradePlayPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    source = await load_upgrade_source(
        user["id"],
        payload.source_id
    )

    target, warning = (
        await find_upgrade_target(
            payload.target_id
        )
    )

    source_price = int(
        source[
            "price_stars"
        ]
    )

    target_price = int(
        target[
            "price_stars"
        ]
    )

    chance = None
    quote_row = None

    if payload.quote_id:
        with db() as conn:
            quote_row = conn.execute("""
            SELECT *
            FROM upgrade_quotes
            WHERE quote_id=?
            AND user_id=?
            """, (
                payload.quote_id,
                user["id"]
            )).fetchone()

        if not quote_row:
            raise HTTPException(
                404,
                "Расчёт апгрейда устарел. Выберите цель заново."
            )

        if int(
            quote_row["used"]
            or 0
        ):
            raise HTTPException(
                409,
                "Этот апгрейд уже был запущен"
            )

        if int(
            quote_row[
                "expires_at"
            ]
        ) < int(time.time()):
            raise HTTPException(
                409,
                "Цена изменилась. Выберите цель ещё раз."
            )

        if str(
            quote_row[
                "source_ref"
            ]
        ) != str(
            source[
                "ref"
            ]
        ):
            raise HTTPException(
                409,
                "Выбран другой подарок"
            )

        if str(
            quote_row[
                "target_id"
            ]
        ) != str(
            target[
                "id"
            ]
        ):
            raise HTTPException(
                409,
                "Выбрана другая цель"
            )

        source_price = int(
            quote_row[
                "source_price_stars"
            ]
        )

        target_price = int(
            quote_row[
                "target_price_stars"
            ]
        )

        chance = float(
            quote_row[
                "chance_percent"
            ]
        )

    else:
        chance = (
            calculate_upgrade_chance(
                source_price,
                target_price
            )
        )

    roll = (
        secrets.randbelow(
            1_000_000
        )
        / 10_000
    )

    won = (
        roll < chance
    )

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        if source["type"] == "deposit":
            current_source = conn.execute("""
            SELECT
                id,
                status,
                hidden
            FROM deposits
            WHERE id=?
            AND user_id=?
            """, (
                source["db_id"],
                user["id"]
            )).fetchone()

            if (
                not current_source
                or current_source["status"]
                != "approved"
                or int(
                    current_source[
                        "hidden"
                    ]
                    or 0
                ) != 0
            ):
                conn.rollback()

                raise HTTPException(
                    409,
                    "Этот подарок уже был использован"
                )

        else:
            current_source = conn.execute("""
            SELECT
                id,
                prize_id,
                status
            FROM case_wins
            WHERE id=?
            AND user_id=?
            """, (
                source["db_id"],
                user["id"]
            )).fetchone()

            if (
                not current_source
                or current_source[
                    "status"
                ] != "owned"
            ):
                conn.rollback()

                raise HTTPException(
                    409,
                    "Этот подарок уже был использован"
                )

        if won:
            existing_claim = (
                conn.execute("""
                SELECT target_id
                FROM upgrade_target_claims
                WHERE target_id=?
                """, (
                    str(
                        target[
                            "id"
                        ]
                    ),
                )).fetchone()
            )

            if existing_claim:
                conn.rollback()

                raise HTTPException(
                    409,
                    (
                        "Эту цель только что забрал другой пользователь. "
                        "Выберите другую."
                    )
                )

        cursor = conn.execute("""
        INSERT INTO upgrade_attempts(
            user_id,
            source_id,
            source_type,
            target_id,
            source_price_stars,
            target_price_stars,
            chance_percent,
            roll_percent,
            won
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            user["id"],
            source["db_id"],
            source["type"],
            str(
                target["id"]
            ),
            source_price,
            target_price,
            chance,
            roll,
            1 if won else 0
        ))

        attempt_id = (
            cursor.lastrowid
        )

        if source["type"] == "deposit":
            conn.execute("""
            UPDATE deposits
            SET
                status='upgrade_spent',
                hidden=1,
                reviewed_at=CURRENT_TIMESTAMP
            WHERE id=?
            AND user_id=?
            """, (
                source["db_id"],
                user["id"]
            ))

        else:
            conn.execute("""
            UPDATE case_wins
            SET
                status='upgrade_spent',
                resolved_at=CURRENT_TIMESTAMP
            WHERE id=?
            AND user_id=?
            """, (
                source["db_id"],
                user["id"]
            ))

            if source.get(
                "prize_id"
            ):
                conn.execute("""
                DELETE FROM upgrade_target_claims
                WHERE target_id=?
                """, (
                    str(
                        source[
                            "prize_id"
                        ]
                    ),
                ))

        win_id = None

        if won:
            try:
                conn.execute("""
                INSERT INTO upgrade_target_claims(
                    target_id,
                    user_id,
                    upgrade_attempt_id
                )
                VALUES(?,?,?)
                """, (
                    str(
                        target[
                            "id"
                        ]
                    ),
                    user["id"],
                    attempt_id
                ))

            except sqlite3.IntegrityError:
                conn.rollback()

                raise HTTPException(
                    409,
                    (
                        "Эта цель уже недоступна. "
                        "Выберите другую."
                    )
                )

            win_cursor = conn.execute("""
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
            VALUES(
                ?,
                0,
                ?,
                ?,
                ?,
                ?,
                0,
                1,
                'owned',
                'UPGRADE',
                0
            )
            """, (
                user["id"],
                str(
                    target[
                        "id"
                    ]
                ),
                str(
                    target.get(
                        "name"
                    )
                    or "Telegram Gift"
                ),
                str(
                    target.get(
                        "image_url"
                    )
                    or ""
                ),
                str(
                    target.get(
                        "gift_url"
                    )
                    or ""
                )
            ))

            win_id = (
                win_cursor.lastrowid
            )

        if payload.quote_id:
            conn.execute("""
            UPDATE upgrade_quotes
            SET used=1
            WHERE quote_id=?
            AND user_id=?
            """, (
                payload.quote_id,
                user["id"]
            ))

        conn.commit()

    return {
        "ok":
            True,
        "won":
            won,
        "success":
            won,
        "chance_percent":
            chance,
        "roll_percent":
            round(
                roll,
                4
            ),
        "source_price_stars":
            source_price,
        "target_price_stars":
            target_price,
        "win_id":
            win_id,
        "target": {
            "id":
                target["id"],
            "name":
                target["name"],
            "image_url":
                target["image_url"],
            "gift_url":
                target["gift_url"],
            "price_stars":
                target_price
        },
        "warning":
            warning
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

    # =====================================================
    # ADMIN NOTIFICATION
    # =====================================================

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

            gift_name = (
                meta.get("name")
                or "Telegram Gift"
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить",
                            callback_data=
                                f"deposit_approve:{deposit_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=
                                f"deposit_reject:{deposit_id}"
                        )
                    ]
                ]
            )

            await bot.send_message(
                ADMIN_ID,
                (
                    "🎁 <b>НОВАЯ ЗАЯВКА НА ПРОВЕРКУ</b>\n\n"
                    f"<b>Заявка:</b> #{deposit_id}\n"
                    f"<b>Пользователь:</b> {html.escape(user_text)}\n"
                    f"<b>Telegram ID:</b> <code>{user['id']}</code>\n"
                    f"<b>Подарок:</b> {html.escape(gift_name)}\n"
                    f"<b>NFT:</b> {html.escape(gift_url)}\n\n"
                    "⏳ <b>Статус:</b> ожидает проверки."
                ),
                parse_mode="HTML",
                reply_markup=keyboard
            )

        except Exception as error:
            print(
                "ADMIN DEPOSIT NOTIFICATION ERROR:",
                repr(error)
            )

    return {
        "ok": True,
        "deposit_id":
            deposit_id,
        "gift":
            meta
    }



@app.post("/api/deposits/sell")
async def sell_deposit(
    payload: DepositSellPayload
):
    user = validate_init_data(
        payload.initData
    )

    save_user(user)

    with db() as conn:
        row = conn.execute("""
        SELECT *
        FROM deposits
        WHERE id=?
        AND user_id=?
        """, (
            payload.deposit_id,
            user["id"]
        )).fetchone()

    if not row:
        raise HTTPException(
            404,
            "Подарок не найден"
        )

    if (
        row["status"] != "approved"
        or int(
            row["hidden"]
            or 0
        ) != 0
    ):
        raise HTTPException(
            409,
            "Этот подарок уже недоступен"
        )

    price_info = (
        await get_gift_price_info(
            row["gift_url"]
        )
    )

    sell_stars = int(
        price_info.get(
            "market_stars",
            0
        )
        or 0
    )

    if sell_stars <= 0:
        raise HTTPException(
            503,
            (
                "Для этого подарка сейчас нет "
                "рыночной цены в Telegram Stars"
            )
        )

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        current = conn.execute("""
        SELECT *
        FROM deposits
        WHERE id=?
        AND user_id=?
        """, (
            payload.deposit_id,
            user["id"]
        )).fetchone()

        if not current:
            conn.rollback()

            raise HTTPException(
                404,
                "Подарок не найден"
            )

        if (
            current["status"] != "approved"
            or int(
                current["hidden"]
                or 0
            ) != 0
        ):
            conn.rollback()

            raise HTTPException(
                409,
                "Этот подарок уже был обработан"
            )

        conn.execute("""
        UPDATE deposits
        SET
            status='sold',
            hidden=1,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        AND user_id=?
        """, (
            payload.deposit_id,
            user["id"]
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
            'deposit_sell_market',
            ?
        )
        """, (
            user["id"],
            sell_stars,
            f"deposit:{payload.deposit_id}"
        ))

        conn.commit()

    return {
        "ok": True,
        "deposit_id":
            payload.deposit_id,
        "credited_stars":
            sell_stars,
        "price_source":
            "telegram_market_stars",
        "balances":
            get_balances(
                user["id"]
            )
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
# DEPOSIT ADMIN CALLBACKS
# =========================================================

@router.callback_query(
    F.data.startswith("deposit_approve:")
)
async def approve_deposit_callback(
    query: CallbackQuery
):
    if not query.from_user:
        return

    if int(query.from_user.id) != int(ADMIN_ID):
        await query.answer(
            "Нет доступа",
            show_alert=True
        )
        return

    try:
        deposit_id = int(
            query.data.split(
                ":",
                1
            )[1]
        )
    except Exception:
        await query.answer(
            "Неверная заявка",
            show_alert=True
        )
        return

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute("""
        SELECT
            d.*,
            u.username,
            u.first_name
        FROM deposits d
        LEFT JOIN users u
            ON u.user_id=d.user_id
        WHERE d.id=?
        """, (
            deposit_id,
        )).fetchone()

        if not row:
            conn.rollback()

            await query.answer(
                "Заявка не найдена",
                show_alert=True
            )
            return

        if row["status"] != "pending":
            conn.rollback()

            await query.answer(
                "Заявка уже обработана",
                show_alert=True
            )
            return

        conn.execute("""
        UPDATE deposits
        SET
            status='approved',
            hidden=0,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            deposit_id,
        ))

        conn.commit()

    try:
        await bot.send_message(
            int(row["user_id"]),
            (
                "✅ <b>Подарок подтверждён!</b>\n\n"
                f"{html.escape(row['gift_name'] or 'Telegram Gift')}\n"
                "Подарок появился в вашем инвентаре."
            ),
            parse_mode="HTML"
        )

    except Exception as error:
        print(
            "USER APPROVE NOTIFICATION ERROR:",
            repr(error)
        )

    if query.message:
        try:
            username = (
                row["username"]
                or ""
            )

            user_text = (
                f"@{username}"
                if username
                else str(
                    row["user_id"]
                )
            )

            await query.message.edit_text(
                (
                    "🎁 <b>ЗАЯВКА НА ПРОВЕРКУ</b>\n\n"
                    f"<b>Заявка:</b> #{deposit_id}\n"
                    f"<b>Пользователь:</b> {html.escape(user_text)}\n"
                    f"<b>Telegram ID:</b> <code>{row['user_id']}</code>\n"
                    f"<b>Подарок:</b> {html.escape(row['gift_name'] or 'Telegram Gift')}\n"
                    f"<b>NFT:</b> {html.escape(row['gift_url'])}\n\n"
                    "✅ <b>Статус: подтверждено</b>"
                ),
                parse_mode="HTML"
            )

        except Exception as error:
            print(
                "ADMIN APPROVE MESSAGE EDIT ERROR:",
                repr(error)
            )

    await query.answer(
        "Подарок подтверждён"
    )


@router.callback_query(
    F.data.startswith("deposit_reject:")
)
async def reject_deposit_callback(
    query: CallbackQuery
):
    if not query.from_user:
        return

    if int(query.from_user.id) != int(ADMIN_ID):
        await query.answer(
            "Нет доступа",
            show_alert=True
        )
        return

    try:
        deposit_id = int(
            query.data.split(
                ":",
                1
            )[1]
        )
    except Exception:
        await query.answer(
            "Неверная заявка",
            show_alert=True
        )
        return

    with db() as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute("""
        SELECT
            d.*,
            u.username,
            u.first_name
        FROM deposits d
        LEFT JOIN users u
            ON u.user_id=d.user_id
        WHERE d.id=?
        """, (
            deposit_id,
        )).fetchone()

        if not row:
            conn.rollback()

            await query.answer(
                "Заявка не найдена",
                show_alert=True
            )
            return

        if row["status"] != "pending":
            conn.rollback()

            await query.answer(
                "Заявка уже обработана",
                show_alert=True
            )
            return

        conn.execute("""
        UPDATE deposits
        SET
            status='rejected',
            hidden=1,
            reviewed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            deposit_id,
        ))

        conn.commit()

    try:
        await bot.send_message(
            int(row["user_id"]),
            (
                "❌ <b>Заявка на подарок отклонена.</b>\n\n"
                f"{html.escape(row['gift_name'] or 'Telegram Gift')}"
            ),
            parse_mode="HTML"
        )

    except Exception as error:
        print(
            "USER REJECT NOTIFICATION ERROR:",
            repr(error)
        )

    if query.message:
        try:
            username = (
                row["username"]
                or ""
            )

            user_text = (
                f"@{username}"
                if username
                else str(
                    row["user_id"]
                )
            )

            await query.message.edit_text(
                (
                    "🎁 <b>ЗАЯВКА НА ПРОВЕРКУ</b>\n\n"
                    f"<b>Заявка:</b> #{deposit_id}\n"
                    f"<b>Пользователь:</b> {html.escape(user_text)}\n"
                    f"<b>Telegram ID:</b> <code>{row['user_id']}</code>\n"
                    f"<b>Подарок:</b> {html.escape(row['gift_name'] or 'Telegram Gift')}\n"
                    f"<b>NFT:</b> {html.escape(row['gift_url'])}\n\n"
                    "❌ <b>Статус: отклонено</b>"
                ),
                parse_mode="HTML"
            )

        except Exception as error:
            print(
                "ADMIN REJECT MESSAGE EDIT ERROR:",
                repr(error)
            )

    await query.answer(
        "Заявка отклонена"
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
