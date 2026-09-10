import asyncio
import os
import random
import sqlite3
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Copy .env.example to .env and set BOT_TOKEN.")

DB = "upgrade.db"
router = Router()

MULTIPLIERS = {
    "1.5": {"multiplier": 1.5, "chance": 0.62},
    "2": {"multiplier": 2.0, "chance": 0.46},
    "3": {"multiplier": 3.0, "chance": 0.29},
    "5": {"multiplier": 5.0, "chance": 0.16},
}

@dataclass
class DemoNFT:
    id: int
    user_id: int
    name: str
    value: int

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
            demo_points INTEGER NOT NULL DEFAULT 1000
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS nfts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value INTEGER NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS upgrade_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_nft_id INTEGER NOT NULL,
            source_value INTEGER NOT NULL,
            target_value INTEGER NOT NULL,
            chance REAL NOT NULL,
            success INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

def ensure_user(message: Message):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES (?, ?)",
            (message.from_user.id, message.from_user.username or "")
        )

def add_demo_nft(user_id: int, value: int):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO nfts(user_id, name, value) VALUES (?, ?, ?)",
            (user_id, f"Demo NFT #{random.randint(1000,9999)}", value)
        )
        return cur.lastrowid

def get_nfts(user_id: int):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, name, value FROM nfts WHERE user_id=? ORDER BY value DESC",
            (user_id,)
        ).fetchall()
    return [DemoNFT(**dict(r)) for r in rows]

def get_nft(user_id: int, nft_id: int):
    with db() as conn:
        row = conn.execute(
            "SELECT id, user_id, name, value FROM nfts WHERE user_id=? AND id=?",
            (user_id, nft_id)
        ).fetchone()
    return DemoNFT(**dict(row)) if row else None

def inventory_keyboard(nfts):
    rows = []
    for nft in nfts:
        rows.append([
            InlineKeyboardButton(
                text=f"{nft.name} · {nft.value} pts",
                callback_data=f"pick:{nft.id}"
            )
        ])
    rows.append([InlineKeyboardButton(text="➕ Получить demo NFT", callback_data="demo")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def multiplier_keyboard(nft_id: int):
    buttons = []
    for key, cfg in MULTIPLIERS.items():
        chance = int(cfg["chance"] * 100)
        buttons.append([
            InlineKeyboardButton(
                text=f"x{key} · шанс {chance}%",
                callback_data=f"upgrade:{nft_id}:{key}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.message(CommandStart())
async def start(message: Message):
    ensure_user(message)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Upgrade", callback_data="inventory")],
        [InlineKeyboardButton(text="🎁 Demo NFT", callback_data="demo")],
    ])
    await message.answer(
        "NFT Upgrade Demo\n\n"
        "Выбирай demo NFT → множитель → пробуй апгрейд.\n"
        "Все предметы и очки тестовые и не имеют реальной стоимости.",
        reply_markup=kb
    )

@router.message(Command("inventory"))
async def inventory_cmd(message: Message):
    ensure_user(message)
    nfts = get_nfts(message.from_user.id)
    if not nfts:
        await message.answer(
            "Инвентарь пуст. Получи demo NFT командой /demo_nft."
        )
        return
    await message.answer("Твой demo-инвентарь:", reply_markup=inventory_keyboard(nfts))

@router.message(Command("demo_nft"))
async def demo_nft_cmd(message: Message):
    ensure_user(message)
    value = random.choice([100, 150, 200, 250, 300])
    nft_id = add_demo_nft(message.from_user.id, value)
    await message.answer(f"Добавлен Demo NFT #{nft_id} стоимостью {value} pts.")

@router.callback_query(F.data == "demo")
async def demo_nft_cb(call: CallbackQuery):
    value = random.choice([100, 150, 200, 250, 300])
    nft_id = add_demo_nft(call.from_user.id, value)
    await call.answer("Demo NFT добавлен")
    nfts = get_nfts(call.from_user.id)
    await call.message.edit_text(
        f"Получен Demo NFT #{nft_id} · {value} pts\n\nТвой инвентарь:",
        reply_markup=inventory_keyboard(nfts)
    )

@router.callback_query(F.data == "inventory")
async def inventory_cb(call: CallbackQuery):
    nfts = get_nfts(call.from_user.id)
    if not nfts:
        await call.message.edit_text(
            "Инвентарь пуст.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Получить demo NFT", callback_data="demo")]
            ])
        )
    else:
        await call.message.edit_text(
            "Выбери NFT для апгрейда:",
            reply_markup=inventory_keyboard(nfts)
        )
    await call.answer()

@router.callback_query(F.data.startswith("pick:"))
async def pick_nft(call: CallbackQuery):
    nft_id = int(call.data.split(":")[1])
    nft = get_nft(call.from_user.id, nft_id)
    if not nft:
        await call.answer("NFT не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"{nft.name}\nСтоимость: {nft.value} pts\n\n"
        "Выбери коэффициент апгрейда:",
        reply_markup=multiplier_keyboard(nft.id)
    )
    await call.answer()

@router.callback_query(F.data.startswith("upgrade:"))
async def upgrade(call: CallbackQuery):
    _, nft_id_s, key = call.data.split(":")
    nft_id = int(nft_id_s)
    nft = get_nft(call.from_user.id, nft_id)
    if not nft or key not in MULTIPLIERS:
        await call.answer("Операция недоступна", show_alert=True)
        return

    cfg = MULTIPLIERS[key]
    target = int(round(nft.value * cfg["multiplier"]))
    chance = cfg["chance"]
    success = random.random() < chance

    with db() as conn:
        # Atomic-ish demo transaction
        current = conn.execute(
            "SELECT value FROM nfts WHERE id=? AND user_id=?",
            (nft.id, call.from_user.id)
        ).fetchone()
        if not current:
            await call.answer("NFT уже использован", show_alert=True)
            return

        conn.execute("DELETE FROM nfts WHERE id=? AND user_id=?", (nft.id, call.from_user.id))

        new_id = None
        if success:
            cur = conn.execute(
                "INSERT INTO nfts(user_id, name, value) VALUES (?, ?, ?)",
                (
                    call.from_user.id,
                    f"Upgraded Demo NFT #{random.randint(1000,9999)}",
                    target,
                )
            )
            new_id = cur.lastrowid

        conn.execute(
            """INSERT INTO upgrade_log(
                user_id, source_nft_id, source_value, target_value, chance, success
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (call.from_user.id, nft.id, nft.value, target, chance, int(success))
        )

    if success:
        text = (
            f"✅ УСПЕХ\n\n"
            f"{nft.value} pts → {target} pts\n"
            f"Новый demo NFT: #{new_id}\n"
            f"Шанс был: {int(chance*100)}%"
        )
    else:
        text = (
            f"❌ НЕУДАЧА\n\n"
            f"Demo NFT на {nft.value} pts сгорел.\n"
            f"Цель была: {target} pts\n"
            f"Шанс был: {int(chance*100)}%"
        )

    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Ещё upgrade", callback_data="inventory")]
        ])
    )
    await call.answer()

@router.message(Command("grant"))
async def grant(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /grant USER_ID VALUE")
        return
    try:
        user_id = int(parts[1])
        value = int(parts[2])
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("USER_ID и VALUE должны быть положительными числами.")
        return
    nft_id = add_demo_nft(user_id, value)
    await message.answer(f"Выдан Demo NFT #{nft_id} пользователю {user_id} на {value} pts.")

async def main():
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
