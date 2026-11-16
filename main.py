import os
import logging
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import MessageHandler, filters
from io import BytesIO
import qrcode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8480522053"))
BOT_NAME = "CaseAltBot"
BRAND = "Powered by GTS Network"
DB_PATH = os.getenv("DB_PATH", "store.db")
CURRENCY = os.getenv("CURRENCY", "₹")
UPI_ID = "aman292011@fam"
UPI_QR_DATA = "upi://pay?pa=aman292011@fam"
USDT_BEP20_ADDRESS = "0x3979A76B7684ad7ea8BDDbafb1E1b95771391D61"
SUPPORT_URL = "https://t.me/gtsuser"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("casealtbot")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price REAL NOT NULL CHECK(price >= 0),
            stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            total REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            kind TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('INR','USDT')),
            paid_amount REAL NOT NULL,
            reference TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            credited_inr REAL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );
        """)
    log.info("Database initialized")


def register_user(tg_user):
    with connect() as conn:
        conn.execute("""
            INSERT INTO users(user_id, username, first_name, balance, joined_at)
            VALUES(?, ?, ?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (tg_user.id, tg_user.username, tg_user.first_name or "", now()))


def get_user(user_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def money(value):
    return f"{CURRENCY}{float(value):,.2f}"


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Browse Shop", callback_data="shop")],
        [
            InlineKeyboardButton("My Balance", callback_data="balance"),
            InlineKeyboardButton("My Orders", callback_data="orders"),
        ],
        [
            InlineKeyboardButton("Add Funds", callback_data="topup"),
            InlineKeyboardButton("Support", callback_data="support"),
        ],
    ])


def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Main Menu", callback_data="home")]])


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != ADMIN_ID:
            if update.callback_query:
                await update.callback_query.answer("Admin access only.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("Admin access only.")
            return
        return await func(update, context)
    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    msg = (
        f"<b>Welcome to {BOT_NAME}</b>\n"
        f"<i>{BRAND}</i>\n\n"
        "Browse available products, check your wallet, or review your orders."
    )
    await update.effective_message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    register_user(q.from_user)
    await q.edit_message_text(
        f"<b>Welcome to {BOT_NAME}</b>\n<i>{BRAND}</i>\n\nChoose an option:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with connect() as conn:
        products = conn.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY category, id"
        ).fetchall()

    if not products:
        await q.edit_message_text(
            "The shop is being updated. Please check again later.",
            reply_markup=back_menu(),
        )
        return

    rows = []
    for p in products:
        label = f"{p['name']} · {money(p['price'])} · Stock: {p['stock']}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"product:{p['id']}")])
    rows.append([InlineKeyboardButton("Main Menu", callback_data="home")])
    await q.edit_message_text(
        "<b>Available Products</b>\nSelect a product to view its details:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        product_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await q.answer("Invalid product.", show_alert=True)
        return

    with connect() as conn:
        p = conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()

    if not p:
        await q.edit_message_text("This product is no longer available.", reply_markup=back_menu())
        return

    text = (
        f"<b>{p['name']}</b>\n"
        f"Category: {p['category']}\n"
        f"Price: {money(p['price'])}\n"
        f"Available quantity: {p['stock']}\n\n"
        f"{p['description'] or 'No additional description.'}\n\n"
        "Orders are placed from your wallet and are reviewed by the administrator."
    )
    buttons = []
    if p["stock"] > 0:
        buttons.append([InlineKeyboardButton("Place Order", callback_data=f"buy:{p['id']}")])
    buttons.append([InlineKeyboardButton("Back to Shop", callback_data="shop")])
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        product_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await q.answer("Invalid product.", show_alert=True)
        return

    with connect() as conn:
        p = conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()
    user = get_user(q.from_user.id)

    if not p or p["stock"] < 1:
        await q.answer("This product is out of stock.", show_alert=True)
        return
    if not user or user["balance"] < p["price"]:
        balance = user["balance"] if user else 0
        await q.answer(
            f"Insufficient balance. Your balance: {money(balance)}",
            show_alert=True,
        )
        return

    # Reserve one unit and debit the wallet atomically.
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()
        wallet = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (q.from_user.id,)
        ).fetchone()

        if not current or current["stock"] < 1:
            conn.rollback()
            await q.answer("This product is now out of stock.", show_alert=True)
            return
        if not wallet or wallet["balance"] < current["price"]:
            conn.rollback()
            await q.answer("Insufficient wallet balance.", show_alert=True)
            return

        conn.execute(
            "UPDATE users SET balance=balance-? WHERE user_id=?",
            (current["price"], q.from_user.id),
        )
        conn.execute(
            "UPDATE products SET stock=stock-1 WHERE id=?",
            (product_id,),
        )
        cur = conn.execute("""
            INSERT INTO orders(user_id, product_id, product_name, quantity, total,
                               status, created_at, updated_at)
            VALUES(?, ?, ?, 1, ?, 'pending', ?, ?)
        """, (
            q.from_user.id, product_id, current["name"],
            current["price"], now(), now()
        ))
        order_id = cur.lastrowid
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, 'purchase', ?, ?)
        """, (q.from_user.id, -current["price"], f"Order #{order_id}", now()))

    await q.answer("Order placed successfully.")
    await q.edit_message_text(
        f"<b>Order placed successfully</b>\n\n"
        f"Order ID: <code>#{order_id}</code>\n"
        f"Product: {current['name']}\n"
        f"Amount: {money(current['price'])}\n"
        "Status: <b>Pending admin fulfilment</b>\n\n"
        "Your order has been recorded. The administrator will update its status.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu(),
    )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"New order #{order_id}\n"
            f"User: {q.from_user.id} (@{q.from_user.username or 'no username'})\n"
            f"Product: {current['name']}\nAmount: {money(current['price'])}\n"
            f"Use /orders to review.",
        )
    except Exception:
        log.exception("Could not notify admin about order %s", order_id)


async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user.id)
    await q.edit_message_text(
        f"<b>My Wallet</b>\nAvailable balance: <b>{money(user['balance'] if user else 0)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu(),
    )


def make_qr(data):
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    bio = BytesIO()
    bio.name = "payment_qr.png"
    image.save(bio, "PNG")
    bio.seek(0)
    return bio


async def topup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("payment_request", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Pay with UPI · INR", callback_data="paymethod:INR")],
        [InlineKeyboardButton("Pay with USDT · BEP20", callback_data="paymethod:USDT")],
        [InlineKeyboardButton("Main Menu", callback_data="home")]
    ])
    await q.edit_message_text(
        "<b>Add Funds to Your Wallet</b>\n\nChoose a payment method. Submit your paid amount and UTR / transaction hash. Wallet credit is subject to admin verification.",
        parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    currency = q.data.split(":", 1)[1]
    context.user_data["payment_request"] = {"currency": currency, "step": "amount"}
    if currency == "INR":
        data = UPI_QR_DATA
        caption = f"<b>UPI · INR</b>\nUPI ID: <code>{UPI_ID}</code>\nScan to pay, then enter the amount paid."
    else:
        data = USDT_BEP20_ADDRESS
        caption = f"<b>USDT · BNB Smart Chain (BEP20)</b>\n<code>{USDT_BEP20_ADDRESS}</code>\nSend USDT on BNB Smart Chain only, then enter the amount paid."
    await q.message.reply_photo(make_qr(data), caption=caption, parse_mode=ParseMode.HTML)
    await q.message.reply_text("Enter the amount you paid (numbers only, e.g. 500 or 10.5).")
    await q.edit_message_text("Payment instructions sent. Complete payment and reply with the amount.", reply_markup=back_menu())


async def payment_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("payment_request")
    if not state:
        return
    raw = (update.effective_message.text or "").strip()
    if state["step"] == "amount":
        try:
            amount = float(raw)
            if amount <= 0 or amount > 100000000:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Enter a valid positive amount.")
            return
        state["amount"] = amount
        state["step"] = "reference"
        await update.effective_message.reply_text("Now send your UTR / transaction reference or blockchain transaction hash.")
        return
    if len(raw) < 4 or len(raw) > 180:
        await update.effective_message.reply_text("Please send a valid transaction reference.")
        return
    currency, amount = state["currency"], state["amount"]
    with connect() as conn:
        cur = conn.execute("INSERT INTO payment_requests(user_id,currency,paid_amount,reference,created_at) VALUES(?,?,?,?,?)", (update.effective_user.id, currency, amount, raw, now()))
        payment_id = cur.lastrowid
    context.user_data.pop("payment_request", None)
    await update.effective_message.reply_text(
        f"<b>Payment submitted</b>\nRequest: <code>#{payment_id}</code>\nPaid: {amount:g} {currency}\nStatus: Pending verification.",
        parse_mode=ParseMode.HTML, reply_markup=back_menu()
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Approve INR payment", callback_data=f"payapprove:{payment_id}"), InlineKeyboardButton("Reject", callback_data=f"payreject:{payment_id}")]])
    try:
        await context.bot.send_message(ADMIN_ID, f"PAYMENT REQUEST #{payment_id}\nUser: {update.effective_user.id}\nMethod: {currency}\nPaid: {amount:g} {currency}\nReference: {raw}\n" + ("Verify BEP20 transaction, then use /creditpayment ID INR_AMOUNT." if currency == "USDT" else "Verify UPI UTR before approval."), reply_markup=keyboard)
    except Exception:
        log.exception("Could not notify admin about payment request %s", payment_id)


@admin_only
async def payment_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, raw_id = q.data.split(":", 1)
    payment_id = int(raw_id)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payment = conn.execute("SELECT * FROM payment_requests WHERE id=?", (payment_id,)).fetchone()
        if not payment or payment["status"] != "pending":
            conn.rollback()
            await q.answer("Already reviewed or not found.", show_alert=True)
            return
        if action == "payapprove" and payment["currency"] == "USDT":
            conn.rollback()
            await q.answer("Verify transfer, then use /creditpayment ID INR_AMOUNT.", show_alert=True)
            return
        credit = payment["paid_amount"] if action == "payapprove" else None
        status = "approved" if credit is not None else "rejected"
        if credit is not None:
            conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (credit, payment["user_id"]))
            conn.execute("INSERT INTO wallet_transactions(user_id,amount,kind,note,created_at) VALUES(?,?,'payment_credit',?,?)", (payment["user_id"], credit, f"Verified UPI payment #{payment_id}", now()))
        conn.execute("UPDATE payment_requests SET status=?,credited_inr=?,reviewed_at=? WHERE id=?", (status, credit, now(), payment_id))
    await q.answer(status.title())
    await q.edit_message_text(f"Payment request #{payment_id}: {status.upper()}.")
    try:
        msg = f"Payment #{payment_id} approved. {money(credit)} added to your wallet." if credit is not None else f"Payment #{payment_id} rejected. Contact support if needed."
        await context.bot.send_message(payment["user_id"], msg)
    except Exception:
        log.exception("Could not notify user about payment review")


@admin_only
async def credit_usdt_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /creditpayment PAYMENT_ID INR_AMOUNT")
        return
    try:
        payment_id, credit = int(context.args[0]), float(context.args[1])
        if credit <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Enter a valid payment ID and INR credit amount.")
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payment = conn.execute("SELECT * FROM payment_requests WHERE id=? AND currency='USDT' AND status='pending'", (payment_id,)).fetchone()
        if not payment:
            conn.rollback()
            await update.effective_message.reply_text("Pending USDT request not found.")
            return
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (credit, payment["user_id"]))
        conn.execute("INSERT INTO wallet_transactions(user_id,amount,kind,note,created_at) VALUES(?,?,'payment_credit',?,?)", (payment["user_id"], credit, f"Verified USDT payment #{payment_id}", now()))
        conn.execute("UPDATE payment_requests SET status='approved',credited_inr=?,reviewed_at=? WHERE id=?", (credit, now(), payment_id))
    await update.effective_message.reply_text(f"USDT request #{payment_id} verified. {money(credit)} credited.")
    await context.bot.send_message(payment["user_id"], f"USDT payment #{payment_id} approved. {money(credit)} added to your wallet.")


async def orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (q.from_user.id,),
        ).fetchall()
    if not rows:
        text = "You have no orders yet."
    else:
        lines = ["<b>Your Recent Orders</b>"]
        for o in rows:
            lines.append(
                f"#{o['id']} · {o['product_name']} · {money(o['total'])} · "
                f"<b>{o['status'].title()}</b>"
            )
        text = "\n".join(lines)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())


async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "For order or wallet support, contact GTS Network Support.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Contact Support", url=SUPPORT_URL)], [InlineKeyboardButton("Main Menu", callback_data="home")]]),
    )


@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        products = conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
        stock = conn.execute("SELECT COALESCE(SUM(stock),0) FROM products WHERE active=1").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        sales = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE status='fulfilled'"
        ).fetchone()[0]
    text = (
        "<b>CaseAltBot · Admin Dashboard</b>\n\n"
        f"Users: {users}\nActive products: {products}\n"
        f"Listed stock units: {stock}\nPending orders: {pending}\n"
        f"Fulfilled order value: {money(sales)}\n\n"
        "<b>Admin commands</b>\n"
        "/addproduct CATEGORY|NAME|DESCRIPTION|PRICE|STOCK\n"
        "/editstock PRODUCT_ID DELTA\n"
        "/setprice PRODUCT_ID PRICE\n"
        "/hide PRODUCT_ID\n"
        "/show PRODUCT_ID\n"
        "/addfunds USER_ID AMOUNT\n"
        "/deductfunds USER_ID AMOUNT\n"
        "/orders\n"
        "/fulfill ORDER_ID\n"
        "/cancelorder ORDER_ID"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args)
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 5:
        await update.effective_message.reply_text(
            "Usage:\n/addproduct CATEGORY|NAME|DESCRIPTION|PRICE|STOCK"
        )
        return
    category, name, description, price_text, stock_text = parts
    try:
        price = float(price_text)
        stock = int(stock_text)
        if price < 0 or stock < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Price and stock must be valid non-negative numbers.")
        return
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO products(category, name, description, price, stock, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
        """, (category, name, description, price, stock, now()))
        product_id = cur.lastrowid
    await update.effective_message.reply_text(
        f"Product added. ID: {product_id}\nName: {name}\nPrice: {money(price)}\nStock: {stock}"
    )


@admin_only
async def edit_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /editstock PRODUCT_ID DELTA")
        return
    try:
        product_id, delta = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("Product ID and delta must be integers.")
        return
    with connect() as conn:
        p = conn.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
        if not p or p["stock"] + delta < 0:
            await update.effective_message.reply_text("Product not found or stock cannot become negative.")
            return
        conn.execute("UPDATE products SET stock=stock+? WHERE id=?", (delta, product_id))
        new_stock = p["stock"] + delta
    await update.effective_message.reply_text(f"Stock updated. New stock: {new_stock}")


@admin_only
async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /setprice PRODUCT_ID PRICE")
        return
    try:
        product_id, price = int(context.args[0]), float(context.args[1])
        if price < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Enter a valid product ID and non-negative price.")
        return
    with connect() as conn:
        cur = conn.execute("UPDATE products SET price=? WHERE id=?", (price, product_id))
    await update.effective_message.reply_text("Price updated." if cur.rowcount else "Product not found.")


@admin_only
async def toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /hide PRODUCT_ID or /show PRODUCT_ID")
        return
    try:
        product_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Product ID must be an integer.")
        return
    active = 0 if update.effective_message.text.startswith("/hide") else 1
    with connect() as conn:
        cur = conn.execute("UPDATE products SET active=? WHERE id=?", (active, product_id))
    await update.effective_message.reply_text("Product updated." if cur.rowcount else "Product not found.")


@admin_only
async def wallet_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.effective_message.text.split()[0].split("@")[0]
    if len(context.args) != 2:
        await update.effective_message.reply_text(f"Usage: {command} USER_ID AMOUNT")
        return
    try:
        user_id = int(context.args[0])
        amount = float(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Enter a valid user ID and positive amount.")
        return
    sign = -1 if command == "/deductfunds" else 1
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            conn.rollback()
            await update.effective_message.reply_text("User not found. They must start the bot first.")
            return
        if sign < 0 and user["balance"] < amount:
            conn.rollback()
            await update.effective_message.reply_text("User does not have enough balance.")
            return
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (sign * amount, user_id))
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, ?, ?, ?)
        """, (user_id, sign * amount, "admin_credit" if sign > 0 else "admin_debit",
              "Admin wallet adjustment", now()))
    await update.effective_message.reply_text(
        f"Wallet updated for {user_id}. Adjustment: {money(sign * amount)}"
    )


@admin_only
async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as conn:
        rows = conn.execute("""
            SELECT o.*, u.username FROM orders o
            LEFT JOIN users u ON u.user_id=o.user_id
            ORDER BY o.id DESC LIMIT 20
        """).fetchall()
    if not rows:
        await update.effective_message.reply_text("No orders found.")
        return
    lines = ["Recent orders:"]
    for o in rows:
        lines.append(
            f"#{o['id']} | {o['product_name']} | {money(o['total'])} | "
            f"{o['status']} | user {o['user_id']}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@admin_only
async def fulfill_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /fulfill ORDER_ID")
        return
    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Order ID must be an integer.")
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order or order["status"] != "pending":
            conn.rollback()
            await update.effective_message.reply_text("Pending order not found.")
            return
        conn.execute(
            "UPDATE orders SET status='fulfilled', updated_at=? WHERE id=?",
            (now(), order_id),
        )
    try:
        await context.bot.send_message(
            order["user_id"],
            f"Your order #{order_id} has been marked as fulfilled. "
            "If you need help, contact support."
        )
    except Exception:
        log.exception("Could not notify user for fulfilled order %s", order_id)
    await update.effective_message.reply_text(f"Order #{order_id} marked fulfilled.")


@admin_only
async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /cancelorder ORDER_ID")
        return
    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Order ID must be an integer.")
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order or order["status"] != "pending":
            conn.rollback()
            await update.effective_message.reply_text("Pending order not found.")
            return
        conn.execute(
            "UPDATE orders SET status='cancelled', updated_at=? WHERE id=?",
            (now(), order_id),
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (order["total"], order["user_id"]),
        )
        conn.execute(
            "UPDATE products SET stock=stock+? WHERE id=?",
            (order["quantity"], order["product_id"]),
        )
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, 'refund', ?, ?)
        """, (order["user_id"], order["total"], f"Refund for order #{order_id}", now()))
    await update.effective_message.reply_text(
        f"Order #{order_id} cancelled. Wallet refunded and stock restored."
    )
    try:
        await context.bot.send_message(
            order["user_id"],
            f"Your order #{order_id} was cancelled. {money(order['total'])} has been refunded."
        )
    except Exception:
        log.exception("Could not notify user for cancelled order %s", order_id)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Unknown command. Use /start to open the shop.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your hosting provider's environment variables.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addproduct", add_product))
    app.add_handler(CommandHandler("editstock", edit_stock))
    app.add_handler(CommandHandler("setprice", set_price))
    app.add_handler(CommandHandler("hide", toggle_product))
    app.add_handler(CommandHandler("show", toggle_product))
    app.add_handler(CommandHandler("addfunds", wallet_adjust))
    app.add_handler(CommandHandler("deductfunds", wallet_adjust))
    app.add_handler(CommandHandler("orders", list_orders))
    app.add_handler(CommandHandler("fulfill", fulfill_order))
    app.add_handler(CommandHandler("cancelorder", cancel_order))
    app.add_handler(CommandHandler("creditpayment", credit_usdt_payment))
    app.add_handler(CallbackQueryHandler(payment_method_callback, pattern="^paymethod:"))
    app.add_handler(CallbackQueryHandler(payment_review_callback, pattern="^payapprove:|^payreject:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, payment_message_handler))

    app.add_handler(CallbackQueryHandler(home_callback, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop$"))
    app.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(topup_callback, pattern="^topup$"))
    app.add_handler(CallbackQueryHandler(orders_callback, pattern="^orders$"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(product_callback, pattern="^product:"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy:"))

    log.info("%s is starting", BOT_NAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
