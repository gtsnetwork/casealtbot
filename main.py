import os
import re
import logging
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO

import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8480522053"))
BOT_NAME = "CaseAltBot"
BRAND = "Powered by GTS Network"
DB_PATH = os.getenv("DB_PATH", "store.db")
CURRENCY = os.getenv("CURRENCY", "₹")

# Existing payment configuration retained.
UPI_ID = "aman292011@fam"
UPI_QR_DATA = "upi://pay?pa=aman292011@fam"
USDT_BEP20_ADDRESS = "0x3979A76B7684ad7ea8BDDbafb1E1b95771391D61"
SUPPORT_URL = "https://t.me/gtsuser"
POLICY_URL = "https://t.me/middlegt/12"

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
        CREATE TABLE IF NOT EXISTS inventory_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            phone_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'available'
                CHECK(status IN ('available','reserved','sold')),
            order_id INTEGER UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_product_status
            ON inventory_numbers(product_id, status);
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
    log.info("Database initialized")


def maintenance_enabled():
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM bot_settings WHERE key='maintenance'"
        ).fetchone()
    return bool(row and row["value"] == "on")


async def block_for_maintenance(query):
    if query.from_user.id != ADMIN_ID and maintenance_enabled():
        await query.answer(
            "CaseAltBot is temporarily unavailable while maintenance is in progress. Customer actions are paused; please try again after maintenance has ended.",
            show_alert=True,
        )
        try:
            await query.edit_message_text(
                f"<b>{BOT_NAME} is under maintenance</b>\n\n"
                "Our team is carrying out maintenance to improve the service. Please return once maintenance is complete.\n\n"
                f"<i>{BRAND}</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return True
    return False


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
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()


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
        [InlineKeyboardButton("Policy / Terms & Conditions", url=POLICY_URL)],
    ])


def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Main Menu", callback_data="home")]
    ])


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != ADMIN_ID:
            if update.callback_query:
                await update.callback_query.answer(
                    "This command is restricted to the CaseAltBot administrator. If you believe you should have access, contact the system administrator.", show_alert=True
                )
            elif update.effective_message:
                await update.effective_message.reply_text("This command is restricted to the CaseAltBot administrator. If you believe you should have access, contact the system administrator.")
            return
        return await func(update, context)
    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID and maintenance_enabled():
        await update.effective_message.reply_text(
            f"{BOT_NAME} is temporarily unavailable while maintenance is in progress. "
            f"Customer access is paused; please return after maintenance is complete.\n\n{BRAND}"
        )
        return
    register_user(update.effective_user)
    msg = (
        f"<b>Welcome to {BOT_NAME}</b>\n"
        f"<i>{BRAND}</i>\n\n"
        "Use the menu below to browse currently available products, view your wallet balance, review recent orders, or contact GTS Network Support."
    )
    await update.effective_message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    register_user(q.from_user)
    await q.edit_message_text(
        f"<b>Welcome to {BOT_NAME}</b>\n<i>{BRAND}</i>\n\nSelect an option below to continue. You can return to this menu at any time using the Main Menu button.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    with connect() as conn:
        products = conn.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY category, id"
        ).fetchall()

    if not products:
        await q.edit_message_text(
            "There are currently no active products available in the shop. Please check again later, or contact support if you need assistance.",
            reply_markup=back_menu(),
        )
        return

    rows = []
    for p in products:
        with connect() as conn:
            available = conn.execute(
                "SELECT COUNT(*) FROM inventory_numbers "
                "WHERE product_id=? AND status='available'",
                (p["id"],),
            ).fetchone()[0]
        label = f"{p['name']} · {money(p['price'])} · Stock: {available}"
        rows.append([
            InlineKeyboardButton(label[:60], callback_data=f"product:{p['id']}")
        ])
    rows.append([InlineKeyboardButton("Main Menu", callback_data="home")])
    await q.edit_message_text(
        "<b>Available Products</b>\nThe products listed below are currently active. Select a product to review its price, description, and available quantity before placing an order:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    try:
        product_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await q.answer("The selected product could not be identified. Return to the shop and select a valid product.", show_alert=True)
        return

    with connect() as conn:
        p = conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()
        available = conn.execute(
            "SELECT COUNT(*) FROM inventory_numbers "
            "WHERE product_id=? AND status='available'",
            (product_id,),
        ).fetchone()[0]

    if not p:
        await q.edit_message_text(
            "This product is no longer active or available for purchase. Return to the shop to browse the current listings.", reply_markup=back_menu()
        )
        return

    text = (
        f"<b>{p['name']}</b>\n"
        f"Category: {p['category']}\n"
        f"Price: {money(p['price'])}\n"
        f"Available quantity: {available}\n\n"
        f"{p['description'] or 'No additional product description has been provided.'}\n\n"
        "Orders are paid for using your available wallet balance. After an order is placed, it remains pending until the administrator completes or cancels it."
    )
    buttons = []
    if available > 0:
        buttons.append([
            InlineKeyboardButton("Place Order", callback_data=f"buy:{p['id']}")
        ])
    buttons.append([InlineKeyboardButton("Back to Shop", callback_data="shop")])
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    try:
        product_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await q.answer("The selected product could not be identified. Return to the shop and select a valid product.", show_alert=True)
        return

    # Reserve a unique phone number and debit the wallet in one transaction.
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        product = conn.execute(
            "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()
        wallet = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (q.from_user.id,)
        ).fetchone()
        inventory = conn.execute(
            "SELECT id, phone_number FROM inventory_numbers "
            "WHERE product_id=? AND status='available' ORDER BY id LIMIT 1",
            (product_id,),
        ).fetchone()

        if not product or not inventory:
            conn.rollback()
            await q.answer("This product currently has no available inventory. Please return to the shop and choose another product, or check back later.", show_alert=True)
            return
        if not wallet or wallet["balance"] < product["price"]:
            conn.rollback()
            balance = wallet["balance"] if wallet else 0
            await q.answer(
                f"Your wallet balance is not sufficient to place this order. Current available balance: {money(balance)}. Add funds to your wallet or choose a lower-priced product.",
                show_alert=True,
            )
            return

        cur = conn.execute("""
            INSERT INTO orders(user_id, product_id, product_name, quantity, total,
                               status, created_at, updated_at)
            VALUES(?, ?, ?, 1, ?, 'pending', ?, ?)
        """, (
            q.from_user.id, product_id, product["name"],
            product["price"], now(), now()
        ))
        order_id = cur.lastrowid

        reserved = conn.execute("""
            UPDATE inventory_numbers
            SET status='reserved', order_id=?
            WHERE id=? AND status='available'
        """, (order_id, inventory["id"]))
        if reserved.rowcount != 1:
            conn.rollback()
            await q.answer(
                "The selected inventory item is no longer available. No order was completed; please try placing your order again.", show_alert=True
            )
            return

        conn.execute(
            "UPDATE users SET balance=balance-? WHERE user_id=?",
            (product["price"], q.from_user.id),
        )
        conn.execute(
            "UPDATE products SET stock=CASE WHEN stock>0 THEN stock-1 ELSE 0 END "
            "WHERE id=?",
            (product_id,),
        )
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, 'purchase', ?, ?)
        """, (q.from_user.id, -product["price"], f"Order #{order_id}", now()))

    await q.answer("Your order has been recorded successfully.")
    await q.edit_message_text(
        f"<b>Order Submitted Successfully</b>\n\n"
        f"Order ID: <code>#{order_id}</code>\n"
        f"Product: {product['name']}\n"
        f"Amount: {money(product['price'])}\n"
        "Status: <b>Pending admin fulfilment</b>\n\n"
        "Your wallet has been charged for this order, and the order is now pending administrator fulfilment. You will receive another message when its status is updated.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu(),
    )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"<b>New Order Alert · #{order_id}</b>\n"
            f"Customer ID: {q.from_user.id} (@{q.from_user.username or 'no username'})\n"
            f"Product: {product['name']}\n"
            f"Reserved phone number: {inventory['phone_number']}\n"
            f"Order amount: {money(product['price'])}\n\n"
            f"Review and fulfil this order manually, then run /fulfill {order_id} to mark it as completed. "
            f"If it cannot be fulfilled, run /cancelorder {order_id} to cancel it, refund the customer, and release the reserved number.",
        )
    except Exception:
        log.exception("Could not notify admin about order %s", order_id)


async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    user = get_user(q.from_user.id)
    await q.edit_message_text(
        f"<b>My Wallet</b>\nThis is the amount currently available for purchases. Pending payment requests are not included until they have been verified and credited.\nAvailable balance: "
        f"<b>{money(user['balance'] if user else 0)}</b>",
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
    if await block_for_maintenance(q):
        return
    await q.answer()
    context.user_data.pop("payment_request", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Pay with UPI · INR", callback_data="paymethod:INR")],
        [InlineKeyboardButton("Pay with USDT · BEP20", callback_data="paymethod:USDT")],
        [InlineKeyboardButton("Main Menu", callback_data="home")]
    ])
    await q.edit_message_text(
        "<b>Add Funds to Your Wallet</b>\n\n"
        "Select the payment method you intend to use. After completing the transfer, submit the exact amount paid and its UTR or blockchain transaction hash. "
        "Funds are added to your wallet only after the administrator verifies the payment.",
        parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    currency = q.data.split(":", 1)[1]
    context.user_data["payment_request"] = {"currency": currency, "step": "amount"}
    if currency == "INR":
        data = UPI_QR_DATA
        caption = (
            f"<b>UPI · INR</b>\nUPI ID: <code>{UPI_ID}</code>\n"
            "Scan the QR code or use the UPI ID shown above to complete your INR payment. After paying, return to this chat and enter the exact amount transferred."
        )
    else:
        data = USDT_BEP20_ADDRESS
        caption = (
            "<b>USDT · BNB Smart Chain (BEP20)</b>\n"
            f"<code>{USDT_BEP20_ADDRESS}</code>\n"
            "Send USDT to the address above using the BNB Smart Chain (BEP20) network only. Transfers sent through another network may not be recoverable. After paying, return to this chat and enter the exact USDT amount transferred."
        )
    await q.message.reply_photo(
        make_qr(data), caption=caption, parse_mode=ParseMode.HTML
    )
    await q.message.reply_text(
        "Enter the exact amount you transferred using numbers only (for example, 500 or 10.5). Do not include the currency symbol."
    )
    await q.edit_message_text(
        "Payment instructions have been sent above. Complete the transfer using the selected method, then reply here with the exact amount paid to continue your verification request.",
        reply_markup=back_menu()
    )


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
            await update.effective_message.reply_text(
                "The amount must be a valid number greater than zero. Enter the exact amount you paid, without a currency symbol, to continue."
            )
            return
        state["amount"] = amount
        state["step"] = "reference"
        await update.effective_message.reply_text(
            "Now send the payment reference for the transfer: the UTR from your UPI transaction, or the transaction hash for a USDT transfer. This reference is required for administrator verification."
        )
        return
    if len(raw) < 4 or len(raw) > 180:
        await update.effective_message.reply_text(
            "The transaction reference must contain between 4 and 180 characters. Please check the UTR or transaction hash and send it again."
        )
        return

    currency, amount = state["currency"], state["amount"]
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO payment_requests(user_id,currency,paid_amount,reference,created_at) "
            "VALUES(?,?,?,?,?)",
            (update.effective_user.id, currency, amount, raw, now())
        )
        payment_id = cur.lastrowid
    context.user_data.pop("payment_request", None)
    await update.effective_message.reply_text(
        f"<b>Payment Request Submitted</b>\nRequest ID: <code>#{payment_id}</code>\n"
        f"Amount reported: {amount:g} {currency}\nStatus: <b>Pending verification</b>\n\n"
        "Your payment details have been sent to the administrator for review. Your wallet will be credited only after the transfer is verified.",
        parse_mode=ParseMode.HTML, reply_markup=back_menu()
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Approve INR payment", callback_data=f"payapprove:{payment_id}"
        ),
        InlineKeyboardButton("Reject", callback_data=f"payreject:{payment_id}")
    ]])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"PAYMENT REQUEST #{payment_id}\nUser: {update.effective_user.id}\n"
            f"Method: {currency}\nPaid: {amount:g} {currency}\nReference: {raw}\n"
            + (
                "Verify BEP20 transaction, then use /creditpayment ID INR_AMOUNT."
                if currency == "USDT"
                else "Verify UPI UTR before approval."
            ),
            reply_markup=keyboard
        )
    except Exception:
        log.exception("Could not notify admin about payment request %s", payment_id)


@admin_only
async def payment_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, raw_id = q.data.split(":", 1)
    payment_id = int(raw_id)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payment = conn.execute(
            "SELECT * FROM payment_requests WHERE id=?", (payment_id,)
        ).fetchone()
        if not payment or payment["status"] != "pending":
            conn.rollback()
            await q.answer("This payment request has already been reviewed, or the request ID does not exist. No further action was taken.", show_alert=True)
            return
        if action == "payapprove" and payment["currency"] == "USDT":
            conn.rollback()
            await q.answer(
                "USDT payments cannot be approved using this button. First verify the transaction on the BNB Smart Chain, then use /creditpayment PAYMENT_ID INR_AMOUNT to credit the verified INR value.",
                show_alert=True
            )
            return
        credit = payment["paid_amount"] if action == "payapprove" else None
        status = "approved" if credit is not None else "rejected"
        if credit is not None:
            conn.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (credit, payment["user_id"])
            )
            conn.execute(
                "INSERT INTO wallet_transactions(user_id,amount,kind,note,created_at) "
                "VALUES(?,?,'payment_credit',?,?)",
                (payment["user_id"], credit,
                 f"Verified UPI payment #{payment_id}", now())
            )
        conn.execute(
            "UPDATE payment_requests SET status=?,credited_inr=?,reviewed_at=? WHERE id=?",
            (status, credit, now(), payment_id)
        )
    await q.answer(status.title())
    await q.edit_message_text(
        f"Payment request #{payment_id} has been marked as {status.upper()}. The customer will be notified of this review result."
    )
    try:
        msg = (
            f"Your payment request #{payment_id} has been approved. {money(credit)} has been credited to your wallet and is now available for purchases."
            if credit is not None
            else f"Your payment request #{payment_id} was not approved, so no funds were added to your wallet. If you believe the payment details are correct, contact GTS Network Support and provide the request ID."
        )
        await context.bot.send_message(payment["user_id"], msg)
    except Exception:
        log.exception("Could not notify user about payment review")


@admin_only
async def credit_usdt_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage: /creditpayment PAYMENT_ID INR_AMOUNT"
        )
        return
    try:
        payment_id, credit = int(context.args[0]), float(context.args[1])
        if credit <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "Enter a valid payment request ID and the verified INR amount to credit. The INR amount must be greater than zero."
        )
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payment = conn.execute(
            "SELECT * FROM payment_requests "
            "WHERE id=? AND currency='USDT' AND status='pending'",
            (payment_id,)
        ).fetchone()
        if not payment:
            conn.rollback()
            await update.effective_message.reply_text(
                "No pending USDT payment request was found for that ID. Check the request ID and confirm that it has not already been reviewed."
            )
            return
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (credit, payment["user_id"])
        )
        conn.execute(
            "INSERT INTO wallet_transactions(user_id,amount,kind,note,created_at) "
            "VALUES(?,?,'payment_credit',?,?)",
            (payment["user_id"], credit,
             f"Verified USDT payment #{payment_id}", now())
        )
        conn.execute(
            "UPDATE payment_requests SET status='approved',credited_inr=?,reviewed_at=? "
            "WHERE id=?",
            (credit, now(), payment_id)
        )
    await update.effective_message.reply_text(
        f"USDT payment request #{payment_id} has been verified. {money(credit)} has been credited to the customer's INR wallet."
    )
    await context.bot.send_message(
        payment["user_id"],
        f"Your USDT payment request #{payment_id} has been verified and approved. {money(credit)} has been credited to your INR wallet and is now available for purchases."
    )


async def orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (q.from_user.id,),
        ).fetchall()
    if not rows:
        text = "You have not placed any orders yet. Once you place an order, its ID, product, total amount, and current fulfilment status will appear here."
    else:
        lines = ["<b>Your Recent Orders</b>"]
        for order in rows:
            lines.append(
                f"#{order['id']} · {order['product_name']} · "
                f"{money(order['total'])} · <b>{order['status'].title()}</b>"
            )
        text = "\n".join(lines)
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_menu()
    )


async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await block_for_maintenance(q):
        return
    await q.answer()
    await q.edit_message_text(
        "For assistance with an order, wallet balance, or payment verification, contact GTS Network Support using the button below. Include the relevant order or payment request ID, and do not share passwords or login credentials.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Contact Support", url=SUPPORT_URL)],
            [InlineKeyboardButton("Main Menu", callback_data="home")]
        ]),
    )


@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        products = conn.execute(
            "SELECT COUNT(*) FROM products WHERE active=1"
        ).fetchone()[0]
        stock = conn.execute(
            "SELECT COUNT(*) FROM inventory_numbers WHERE status='available'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='pending'"
        ).fetchone()[0]
        sales = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE status='fulfilled'"
        ).fetchone()[0]
    text = (
        "<b>CaseAltBot · Admin Dashboard</b>\n\n"
        f"Users: {users}\nActive products: {products}\n"
        f"Available phone numbers: {stock}\nPending orders: {pending}\n"
        f"Fulfilled order value: {money(sales)}\n"
        f"Maintenance: {'ON' if maintenance_enabled() else 'OFF'}\n\n"
        "<b>Admin commands</b>\n"
        "/addproduct CATEGORY|NAME|DESCRIPTION|PRICE|STOCK\n"
        "/addnumber PRODUCT_ID\n"
        "/numbers PRODUCT_ID\n"
        "/cancel (cancel number upload)\n"
        "/maintenance on\n"
        "/maintenance off\n"
        "/editstock PRODUCT_ID DELTA\n"
        "/setprice PRODUCT_ID PRICE\n"
        "/hide PRODUCT_ID\n"
        "/show PRODUCT_ID\n"
        "/addfunds USER_ID AMOUNT\n"
        "/deductfunds USER_ID AMOUNT\n"
        "/orders\n"
        "/fulfill ORDER_ID\n"
        "/cancelorder ORDER_ID\n"
        "/creditpayment PAYMENT_ID INR_AMOUNT"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args)
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 5:
        await update.effective_message.reply_text(
            "Usage:\n/addproduct CATEGORY|NAME|DESCRIPTION|PRICE|STOCK\n"
            "Separate the five fields with |. For phone-number inventory, set STOCK to 0 and add each number separately with /addnumber PRODUCT_ID."
        )
        return
    category, name, description, price_text, stock_text = parts
    try:
        price = float(price_text)
        stock = int(stock_text)
        if price < 0 or stock < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "Enter a valid price of zero or more and a whole-number stock count of zero or more. Do not include a currency symbol in the price."
        )
        return
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO products(category, name, description, price, stock, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
        """, (category, name, description, price, stock, now()))
        product_id = cur.lastrowid
    await update.effective_message.reply_text(
        f"<b>Product created successfully</b>\nProduct ID: {product_id}\nName: {name}\n"
        f"Price: {money(price)}\nDisplayed stock: {stock}\n\n"
        "Customers can place orders only after individual phone numbers have been added to this product's inventory."
    )


@admin_only
async def add_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /addnumber PRODUCT_ID\nProvide the ID of an existing product to begin adding phone numbers to its inventory.")
        return
    try:
        product_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("The product ID must be a whole number. Check the product ID and run the command again.")
        return
    with connect() as conn:
        product = conn.execute(
            "SELECT id, name FROM products WHERE id=?", (product_id,)
        ).fetchone()
    if not product:
        await update.effective_message.reply_text("No product was found with that ID. Check the ID using the admin product records, then try again.")
        return
    context.user_data["inventory_upload_product_id"] = product_id
    await update.effective_message.reply_text(
        f"Send one phone number to add it to product #{product_id} ({product['name']}).\n"
        "Include the country code (for example, +919876543210) and send the number by itself, with no extra text.\n"
        "To stop this upload process, use /cancel."
    )


@admin_only
async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("inventory_upload_product_id", None)
    await update.effective_message.reply_text("The current inventory upload session has been cancelled. No additional phone number will be added unless you start a new upload with /addnumber.")


@admin_only
async def list_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /numbers PRODUCT_ID\nProvide the ID of an existing product to view its most recently added inventory numbers and their statuses.")
        return
    try:
        product_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("The product ID must be a whole number. Check the product ID and run the command again.")
        return
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, phone_number, status, order_id FROM inventory_numbers "
            "WHERE product_id=? ORDER BY id DESC LIMIT 100",
            (product_id,)
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text(
            f"No inventory numbers are currently recorded for product #{product_id}. Add numbers with /addnumber {product_id} before accepting customer orders."
        )
        return
    labels = {
        "available": "Available",
        "reserved": "Reserved",
        "sold": "Sold",
    }
    lines = [f"Inventory for product #{product_id}:"]
    for row in rows:
        order_label = f"#{row['order_id']}" if row["order_id"] else "—"
        lines.append(
            f"Inventory ID: {row['id']} | Phone: {row['phone_number']} | "
            f"Status: {labels[row['status']]} | Order: {order_label}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@admin_only
async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text(
            "Usage: /maintenance on OR /maintenance off\nUse /maintenance on to pause customer access during maintenance, or /maintenance off to restore customer access."
        )
        return
    value = context.args[0].lower()
    with connect() as conn:
        conn.execute(
            "INSERT INTO bot_settings(key,value) VALUES('maintenance',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,)
        )
    await update.effective_message.reply_text(
        "Maintenance mode is now enabled. Customer-facing commands and actions are paused; administrator commands remain available."
        if value == "on"
        else "Maintenance mode is now disabled. Customer-facing commands and actions are available again."
    )


async def inventory_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_id = context.user_data.get("inventory_upload_product_id")
    if product_id is None or update.effective_user.id != ADMIN_ID:
        return
    raw = (update.effective_message.text or "").strip()
    if not re.fullmatch(r"\+?[1-9]\d{6,14}", raw):
        await update.effective_message.reply_text(
            "The phone number format is invalid. Send one number only, including its country code (for example, +919876543210). The number must contain 7 to 15 digits."
        )
        return
    phone = raw if raw.startswith("+") else f"+{raw}"
    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT id FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if not exists:
                conn.rollback()
                context.user_data.pop("inventory_upload_product_id", None)
                await update.effective_message.reply_text(
                    "The selected product no longer exists. This inventory upload session has been cancelled, and no number was added."
                )
                return
            conn.execute(
                "INSERT INTO inventory_numbers(product_id,phone_number,status,created_at) "
                "VALUES(?,?,'available',?)",
                (product_id, phone, now())
            )
            conn.execute(
                "UPDATE products SET stock=stock+1 WHERE id=?", (product_id,)
            )
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text(
            "This phone number is already recorded in inventory and was not added again. Send a different number to continue, or use /cancel to end the upload session."
        )
        return

    context.user_data.pop("inventory_upload_product_id", None)
    await update.effective_message.reply_text(
        f"Phone number added to inventory successfully.\nProduct ID: {product_id}\n"
        f"Phone number: {phone}\nInventory status: Available"
    )


@admin_only
async def edit_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage: /editstock PRODUCT_ID DELTA\nProvide a product ID and a whole-number adjustment.\n"
            "This changes the legacy displayed stock only; orders still require "
            "an available number in inventory."
        )
        return
    try:
        product_id, delta = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text(
            "The product ID and stock adjustment (delta) must both be whole numbers. Use a positive delta to increase the displayed count or a negative delta to decrease it."
        )
        return
    with connect() as conn:
        product = conn.execute(
            "SELECT stock FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not product or product["stock"] + delta < 0:
            await update.effective_message.reply_text(
                "The product ID was not found, or this adjustment would make the displayed stock count negative. Check the product ID and current stock before trying again."
            )
            return
        conn.execute(
            "UPDATE products SET stock=stock+? WHERE id=?",
            (delta, product_id)
        )
        new_stock = product["stock"] + delta
    await update.effective_message.reply_text(
        f"The displayed stock count has been updated successfully. New displayed stock value: {new_stock}. This adjustment does not change the individual phone numbers stored in inventory."
    )


@admin_only
async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage: /setprice PRODUCT_ID PRICE\nProvide an existing product ID and the new non-negative price. The updated price will apply to future orders."
        )
        return
    try:
        product_id, price = int(context.args[0]), float(context.args[1])
        if price < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "Enter a whole-number product ID and a valid price of zero or more. The price must be a numeric value without a currency symbol."
        )
        return
    with connect() as conn:
        cur = conn.execute(
            "UPDATE products SET price=? WHERE id=?", (price, product_id)
        )
    await update.effective_message.reply_text(
        "The product price has been updated successfully. The new price will be shown in the shop for future orders." if cur.rowcount else "Product not found."
    )


@admin_only
async def toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /hide PRODUCT_ID or /show PRODUCT_ID\nUse /hide to remove a product from the customer shop, or /show to make it visible again."
        )
        return
    try:
        product_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "The product ID must be a whole number. Check the ID and run the command again."
        )
        return
    active = 0 if update.effective_message.text.split()[0].split("@")[0] == "/hide" else 1
    with connect() as conn:
        cur = conn.execute(
            "UPDATE products SET active=? WHERE id=?", (active, product_id)
        )
    await update.effective_message.reply_text(
        "The product listing has been updated successfully." if cur.rowcount else "Product not found."
    )


@admin_only
async def wallet_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.effective_message.text.split()[0].split("@")[0]
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            f"Usage: {command} USER_ID AMOUNT"
        )
        return
    try:
        user_id = int(context.args[0])
        amount = float(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "Enter a valid numeric user ID and an adjustment amount greater than zero. Do not include the currency symbol."
        )
        return
    sign = -1 if command == "/deductfunds" else 1
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user:
            conn.rollback()
            await update.effective_message.reply_text(
                "No registered user was found with that ID. The user must open CaseAltBot and run /start before their wallet can be adjusted."
            )
            return
        if sign < 0 and user["balance"] < amount:
            conn.rollback()
            await update.effective_message.reply_text(
                "The user's current wallet balance is lower than the amount requested for deduction. No wallet adjustment was made."
            )
            return
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (sign * amount, user_id)
        )
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, ?, ?, ?)
        """, (
            user_id, sign * amount,
            "admin_credit" if sign > 0 else "admin_debit",
            "Admin wallet adjustment", now()
        ))
    await update.effective_message.reply_text(
        f"Wallet adjustment completed for user ID {user_id}. Adjustment applied: {money(sign * amount)}. This amount has been recorded in the wallet transaction history."
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
        await update.effective_message.reply_text("No orders have been recorded yet. New customer orders will appear here with their order ID, amount, customer ID, and current status.")
        return
    lines = ["<b>Recent Orders</b>\nThe latest orders are listed below with their product, total value, current status, and customer ID:"]
    for order in rows:
        lines.append(
            f"#{order['id']} | {order['product_name']} | "
            f"{money(order['total'])} | {order['status']} | user {order['user_id']}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@admin_only
async def fulfill_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /fulfill ORDER_ID\nProvide the ID of a pending order after you have completed its fulfilment.")
        return
    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Order ID must be an integer.")
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order or order["status"] != "pending":
            conn.rollback()
            await update.effective_message.reply_text(
                "No pending order was found for that ID. Check the order ID and confirm that the order has not already been fulfilled or cancelled."
            )
            return
        conn.execute(
            "UPDATE orders SET status='fulfilled', updated_at=? WHERE id=?",
            (now(), order_id)
        )
        conn.execute(
            "UPDATE inventory_numbers SET status='sold' "
            "WHERE order_id=? AND status='reserved'",
            (order_id,)
        )
    try:
        await context.bot.send_message(
            order["user_id"],
            f"Your order #{order_id} has been marked as fulfilled by the administrator. If you have a question about this order, contact GTS Network Support."
        )
    except Exception:
        log.exception(
            "Could not notify user for fulfilled order %s", order_id
        )
    await update.effective_message.reply_text(
        f"Order #{order_id} has been marked as fulfilled. The phone number reserved for this order has been moved to Sold inventory."
    )


@admin_only
async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage: /cancelorder ORDER_ID\nProvide the ID of a pending order to cancel it, return its full amount to the customer's wallet, and release its reserved inventory number."
        )
        return
    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Order ID must be an integer.")
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order or order["status"] != "pending":
            conn.rollback()
            await update.effective_message.reply_text(
                "No pending order was found for that ID. Check the order ID and confirm that the order has not already been fulfilled or cancelled."
            )
            return
        conn.execute(
            "UPDATE orders SET status='cancelled', updated_at=? WHERE id=?",
            (now(), order_id)
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (order["total"], order["user_id"])
        )
        conn.execute(
            "UPDATE products SET stock=stock+? WHERE id=?",
            (order["quantity"], order["product_id"])
        )
        conn.execute(
            "UPDATE inventory_numbers SET status='available', order_id=NULL "
            "WHERE order_id=? AND status='reserved'",
            (order_id,)
        )
        conn.execute("""
            INSERT INTO wallet_transactions(user_id, amount, kind, note, created_at)
            VALUES(?, ?, 'refund', ?, ?)
        """, (
            order["user_id"], order["total"],
            f"Refund for order #{order_id}", now()
        ))
    await update.effective_message.reply_text(
        f"Order #{order_id} has been cancelled successfully. The full order amount has been returned to the customer's wallet, and the reserved phone number has been restored to available inventory."
    )
    try:
        await context.bot.send_message(
            order["user_id"],
            f"Your order #{order_id} was cancelled by the administrator. "
            f"The full amount of {money(order['total'])} has been returned to your wallet. The reserved inventory number has also been released."
        )
    except Exception:
        log.exception(
            "Could not notify user for cancelled order %s", order_id
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Set it in your hosting provider's environment variables."
        )
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addproduct", add_product))
    app.add_handler(CommandHandler("addnumber", add_number))
    app.add_handler(CommandHandler("numbers", list_numbers))
    app.add_handler(CommandHandler("maintenance", maintenance_command))
    app.add_handler(CommandHandler("cancel", cancel_upload))
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

    app.add_handler(
        CallbackQueryHandler(payment_method_callback, pattern="^paymethod:")
    )
    app.add_handler(
        CallbackQueryHandler(
            payment_review_callback, pattern="^payapprove:|^payreject:"
        )
    )

    # Group -1 handles pending admin number uploads before the payment text handler.
    # Both handlers can run in their respective groups without blocking each other.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, inventory_message_handler
        ),
        group=-1,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, payment_message_handler
        ),
        group=0,
    )

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
