# NFT Upgrade Telegram Bot — demo/test version

This is a Telegram bot prototype for an NFT "upgrade" game using virtual, non-redeemable demo items.

## Features
- User profile with demo balance
- Demo NFT inventory
- Upgrade mechanic
- Select target multiplier: x1.5, x2, x3, x5
- Win probability displayed before play
- Successful upgrade replaces source item with a higher-value demo NFT
- Failed upgrade removes the demo NFT
- Admin `/grant <user_id> <value>` command to grant a demo NFT
- SQLite database

## Important
This starter intentionally does NOT:
- accept real money;
- custody real NFTs;
- connect wallets;
- perform on-chain transfers;
- pay out prizes of real-world value.

Before deploying any real-money/NFT wagering product, obtain jurisdiction-specific legal and compliance advice and implement age/geographic restrictions, licensing, AML/KYC, responsible-gambling controls, and security reviews as applicable.

## Setup

1. Create a bot with @BotFather and copy the token.
2. Install Python 3.11+.
3. Create `.env` from `.env.example`.
4. Install dependencies:
   `pip install -r requirements.txt`
5. Run:
   `python bot.py`

## Commands
- `/start`
- `/inventory`
- `/demo_nft`
- `/grant USER_ID VALUE` — admin only

