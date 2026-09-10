# FARTOV NFT BOT v2

Telegram bot + Mini App for collectible gift deposits.

Works now:
- Mini App
- Telegram initData verification
- Deposit instructions for @fart2_backpack
- User submits public collectible gift link: https://t.me/nft/<slug>
- Admin approves/rejects deposits in Telegram
- Approved gifts appear in Mini App inventory

Important:
A normal Telegram bot cannot automatically inspect all gifts arriving to a regular personal Telegram account. This build therefore uses transfer -> gift link -> admin verification.

Railway variables:
BOT_TOKEN
ADMIN_ID
DEPOSIT_USERNAME=fart2_backpack
WEBAPP_URL=<your Railway public HTTPS domain>
PORT=8080

Start command:
python app.py
