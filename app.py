
import asyncio, hashlib, hmac, json, os, sqlite3, time
from urllib.parse import parse_qsl
import uvicorn
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
ADMIN_ID=int(os.getenv("ADMIN_ID","0") or 0)
WEBAPP_URL=os.getenv("WEBAPP_URL","").strip()
DEPOSIT_USERNAME=os.getenv("DEPOSIT_USERNAME","fart2_backpack").lstrip("@")
PORT=int(os.getenv("PORT","8080"))
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing")

DB="fartov_v2.db"
app=FastAPI()
bot=Bot(BOT_TOKEN)
router=Router()
dp=Dispatcher()
dp.include_router(router)

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            gift_url TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at DATETIME)""")

def validate_init_data(init_data):
    if not init_data: raise HTTPException(401,"Missing Telegram initData")
    pairs=dict(parse_qsl(init_data,keep_blank_values=True))
    recv=pairs.pop("hash",None)
    if not recv: raise HTTPException(401,"Missing hash")
    auth=int(pairs.get("auth_date","0"))
    if not auth or abs(int(time.time())-auth)>86400: raise HTTPException(401,"Expired initData")
    check="\n".join(f"{k}={v}" for k,v in sorted(pairs.items()))
    secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
    calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc,recv): raise HTTPException(401,"Invalid initData")
    try: return json.loads(pairs["user"])
    except: raise HTTPException(401,"Missing user")

def save_user(u):
    with db() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name""",
        (u["id"],u.get("username",""),u.get("first_name","")))

class InitPayload(BaseModel): initData:str
class DepositPayload(BaseModel):
    initData:str
    gift_url:str

@app.get("/")
async def index(): return FileResponse("static/index.html")

@app.get("/health")
async def health(): return {"ok":True}

@app.post("/api/me")
async def me(p:InitPayload):
    u=validate_init_data(p.initData); save_user(u)
    with db() as c:
        rows=c.execute("""SELECT id,gift_url,status,created_at,reviewed_at
        FROM deposits WHERE user_id=? ORDER BY id DESC""",(u["id"],)).fetchall()
    return {"user":{"id":u["id"],"first_name":u.get("first_name",""),"username":u.get("username","")},
            "deposit_username":"@"+DEPOSIT_USERNAME,
            "deposits":[dict(r) for r in rows]}

@app.post("/api/deposit")
async def deposit(p:DepositPayload):
    u=validate_init_data(p.initData); save_user(u)
    url=p.gift_url.strip()
    if url.startswith("t.me/"): url="https://"+url
    if not url.startswith("https://t.me/nft/"):
        raise HTTPException(400,"Вставь ссылку вида https://t.me/nft/...")
    try:
        with db() as c:
            cur=c.execute("INSERT INTO deposits(user_id,gift_url) VALUES(?,?)",(u["id"],url))
            dep_id=cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409,"Этот подарок уже зарегистрирован")
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID,
                f"🎁 Новый депозит\n\nID: {dep_id}\nUser: {u['id']} @{u.get('username','')}\nGift: {url}\n\n"
                f"Проверь, что подарок находится у @{DEPOSIT_USERNAME}.\n"
                f"/approve {dep_id}\n/reject {dep_id}")
        except: pass
    return {"ok":True,"deposit_id":dep_id}

@router.message(CommandStart())
async def start(m:Message):
    buttons=[]
    if WEBAPP_URL.startswith("https://"):
        buttons.append([InlineKeyboardButton(text="🎮 OPEN MINI APP",web_app=WebAppInfo(url=WEBAPP_URL))])
    buttons.append([InlineKeyboardButton(text="🎁 @"+DEPOSIT_USERNAME,url="https://t.me/"+DEPOSIT_USERNAME)])
    text=f"FARTOV NFT\n\nCollectible gifts принимаются на @{DEPOSIT_USERNAME}."
    if not WEBAPP_URL.startswith("https://"): text+="\n\nДобавь WEBAPP_URL в Railway."
    await m.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

def is_admin(m): return ADMIN_ID and m.from_user.id==ADMIN_ID

@router.message(Command("pending"))
async def pending(m:Message):
    if not is_admin(m): return
    with db() as c:
        rows=c.execute("SELECT id,user_id,gift_url FROM deposits WHERE status='pending' ORDER BY id LIMIT 20").fetchall()
    await m.answer("Нет ожидающих депозитов." if not rows else
                   "\n\n".join(f"ID {r['id']} • user {r['user_id']}\n{r['gift_url']}" for r in rows))

@router.message(Command("approve"))
async def approve(m:Message):
    if not is_admin(m): return
    parts=m.text.split()
    if len(parts)!=2 or not parts[1].isdigit():
        await m.answer("Формат: /approve ID"); return
    dep=int(parts[1])
    with db() as c:
        row=c.execute("SELECT user_id,gift_url,status FROM deposits WHERE id=?",(dep,)).fetchone()
        if not row: await m.answer("Депозит не найден."); return
        if row["status"]!="pending": await m.answer("Он уже обработан."); return
        c.execute("UPDATE deposits SET status='approved',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(dep,))
    await m.answer(f"✅ Депозит {dep} подтвержден.")
    try: await bot.send_message(row["user_id"],f"✅ Подарок подтвержден:\n{row['gift_url']}")
    except: pass

@router.message(Command("reject"))
async def reject(m:Message):
    if not is_admin(m): return
    parts=m.text.split(maxsplit=2)
    if len(parts)<2 or not parts[1].isdigit():
        await m.answer("Формат: /reject ID [причина]"); return
    dep=int(parts[1]); note=parts[2] if len(parts)>2 else "Не подтверждено"
    with db() as c:
        row=c.execute("SELECT user_id FROM deposits WHERE id=?",(dep,)).fetchone()
        if not row: await m.answer("Депозит не найден."); return
        c.execute("UPDATE deposits SET status='rejected',admin_note=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(note,dep))
    await m.answer(f"❌ Депозит {dep} отклонен.")
    try: await bot.send_message(row["user_id"],f"❌ Депозит отклонен.\n{note}")
    except: pass

async def run_bot(): await dp.start_polling(bot)
async def run_web():
    s=uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=PORT,log_level="info"))
    await s.serve()

async def main():
    init_db()
    await asyncio.gather(run_web(),run_bot())

if __name__=="__main__": asyncio.run(main())
