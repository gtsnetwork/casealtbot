# CaseAltBot — Powered by GTS Network

A Telegram storefront with a SQLite-backed wallet, product catalogue, stock counts, order tracking and admin controls.

## Features

- User registration and wallet balance
- Product categories, descriptions, prices and stock quantities
- Atomic wallet debit and stock reservation when an order is placed
- Admin order review, fulfilment status and cancellation/refund
- Admin wallet adjustments
- SQLite database with WAL mode

This version records orders and supports admin-controlled fulfilment. It does not collect or distribute third-party account passwords, login codes, session strings or authentication tokens.

## Setup

1. Create a Telegram bot with BotFather.
2. Copy `.env.example` to `.env` for local development.
3. Set `BOT_TOKEN` and `ADMIN_ID` in your host's environment variables.
4. Install dependencies: `pip install -r requirements.txt`
5. Start: `python main.py`

Do not commit your real bot token or the SQLite database.

## Admin commands

- `/admin` — dashboard and command guide
- `/addproduct CATEGORY|NAME|DESCRIPTION|PRICE|STOCK`
- `/editstock PRODUCT_ID DELTA` — add or subtract stock
- `/setprice PRODUCT_ID PRICE`
- `/hide PRODUCT_ID` / `/show PRODUCT_ID`
- `/addfunds USER_ID AMOUNT`
- `/deductfunds USER_ID AMOUNT`
- `/orders`
- `/fulfill ORDER_ID`
- `/cancelorder ORDER_ID` — refunds wallet and restores stock

### Example product

`/addproduct Software|Design Template|Editable template pack|199|15`

## Hosting

For a worker host, use the included Procfile or set the start command to:

`python main.py`

Use persistent storage for `store.db`; ephemeral filesystems can erase the database on redeploy. Keep exactly one running bot worker for this polling-based version.
