import os, logging
from telegram import Update, ParseMode
from telegram.ext import CommandHandler, CallbackContext
from datetime import datetime, timezone
from db import get_cursor, init_db   # NEW

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Bot Configuration
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
CM_USER_ID         = int(os.environ.get('CM_USER_ID', '0'))   # set in Railway

# Menu kept in code
MENU = {
    'coffee':   {'name': 'Coffee',        'price': 2.50},
    'tea':      {'name': 'Tea',           'price': 2.00},
    'sandwich': {'name': 'Sandwich',      'price': 5.00},
    'burger':   {'name': 'Burger',        'price': 8.00},
    'pizza':    {'name': 'Pizza Slice',   'price': 4.50},
    'salad':    {'name': 'Salad',         'price': 6.00},
    'juice':    {'name': 'Fresh Juice',   'price': 3.50},
    'cake':     {'name': 'Cake Slice',    'price': 4.00},
}

init_db()   # ensure tables exist

# ------------------------------------------------------------------
# Helper SQL helpers
# ------------------------------------------------------------------
def get_user_display_name(user):
    return f"@{user.username}" if user.username else user.first_name

def is_cm(user_id: int) -> bool:
    return user_id == CM_USER_ID

def notify_cm(context: CallbackContext, text: str, parse_mode=ParseMode.MARKDOWN):
    try:
        if CM_USER_ID:
            context.bot.send_message(chat_id=CM_USER_ID, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.warning("CM notification failed: %s", e)

# ------------------------------------------------------------------
# Command handlers (unchanged signatures, new SQL inside)
# ------------------------------------------------------------------
def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if is_cm(user.id):
        txt = ("Welcome back, Cafeteria Manager! 👨‍🍳\n"
               "/menu /orders /clients /received /pending /sales /balance /summary /help")
    else:
        txt = (f"Hi {user.first_name}! 🍽️\n"
               "/menu /order /paid /balance /summary /help")
    update.message.reply_text(txt)

def menu_command(update: Update, context: CallbackContext) -> None:
    lines = ["🍽️ **CAFETERIA MENU** 🍽️\n"]
    for code, item in MENU.items():
        lines.append(f"**{item['name']}** – ${item['price']:.2f}\n`/order {code} <qty>`")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def order_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if is_cm(user.id):
        update.message.reply_text("Managers don't order!")
        return
    if len(context.args) < 2:
        update.message.reply_text("Usage: /order <item_code> <quantity>")
        return
    code, qty_str = context.args[0].lower(), context.args[1]
    if code not in MENU:
        update.message.reply_text("Item not found. /menu")
        return
    try:
        qty = int(qty_str)
        if qty <= 0: raise ValueError
    except ValueError:
        update.message.reply_text("Quantity must be a positive integer.")
        return

    item = MENU[code]
    total = qty * item['price']
    with get_cursor() as cur:
        # upsert client
        cur.execute("""
            INSERT INTO clients (user_id, user_name, last_order)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET last_order=EXCLUDED.last_order;
        """, (user.id, get_user_display_name(user)))
        # insert order
        cur.execute("""
            INSERT INTO orders
            (user_id, item_code, item_name, quantity, price_per_item, total_price)
            VALUES (%s,%s,%s,%s,%s,%s);
        """, (user.id, code, item['name'], qty, item['price'], total))
    update.message.reply_markdown(
        f"✅ **Order placed**\n{qty}× {item['name']} = ${total:.2f}"
    )
    notify_cm(context, f"🆕 **{get_user_display_name(user)}** ordered {qty}× {item['name']} (${total:.2f})")

# ------------------------------------------------------------------
def paid_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if is_cm(user.id):
        update.message.reply_text("Use /received")
        return
    if not context.args:
        update.message.reply_text("Usage: /paid <amount>")
        return
    try:
        amt = float(context.args[0])
        if amt <= 0: raise ValueError
    except ValueError:
        update.message.reply_text("Amount must be a positive number.")
        return

    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO pending_payments (user_id, user_name, amount)
            VALUES (%s,%s,%s)
        """, (user.id, get_user_display_name(user), amt))
    update.message.reply_text("💰 Payment reported, pending manager confirmation.")
    notify_cm(context, f"💰 **{get_user_display_name(user)}** reported ${amt:.2f} /received")

# ------------------------------------------------------------------
def orders_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    with get_cursor() as cur:
        cur.execute("""
            SELECT c.user_name, o.created_at, o.quantity, o.item_name, o.total_price
            FROM orders o
            JOIN clients c ON c.user_id = o.user_id
            ORDER BY o.created_at DESC
            LIMIT 20;
        """)
        rows = cur.fetchall()
    if not rows:
        update.message.reply_text("No orders.")
        return
    lines = ["📋 **RECENT ORDERS**"]
    for uname, ts, qty, iname, total in rows:
        tstr = ts.strftime('%m-%d %H:%M') if ts else 'N/A'
        lines.append(f"{uname} – {qty}× {iname} – ${total:.2f} _{tstr}_")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def received_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    with get_cursor() as cur:
        cur.execute("SELECT id, user_id, user_name, amount FROM pending_payments ORDER BY created_at")
        pend = cur.fetchall()
    if not pend:
        update.message.reply_text("No pending.")
        return
    if not context.args:
        lines = ["💰 **PENDING**"]
        for idx, (_, _, uname, amt) in enumerate(pend, 1):
            lines.append(f"{idx}. {uname} – ${amt:.2f}")
        lines.append("Use /received <num>")
        update.message.reply_markdown("\n".join(lines))
        return
    try:
        idx = int(context.args[0]) - 1
        pid, uid, uname, amt = pend[idx]
    except (IndexError, ValueError):
        update.message.reply_text("Invalid number.")
        return
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO payments (user_id, amount, confirmed_by_cm, original_ts)
            VALUES (%s,%s,true, NOW())
        """, (uid, amt))
        cur.execute("DELETE FROM pending_payments WHERE id=%s", (pid,))
    update.message.reply_text(f"✅ Confirmed ${amt:.2f} from {uname}")
    try:
        context.bot.send_message(chat_id=uid, text=f"✅ Your payment of ${amt:.2f} was confirmed!")
    except Exception as e:
        logger.warning("Could not DM user: %s", e)

# ------------------------------------------------------------------
def pending_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    with get_cursor() as cur:
        cur.execute("SELECT user_name, amount FROM pending_payments ORDER BY created_at")
        rows = cur.fetchall()
    if not rows:
        update.message.reply_text("✅ All clear!")
        return
    total = sum(a for _, a in rows)
    lines = [f"💰 **PENDING (${total:.2f})**"]
    for uname, amt in rows:
        lines.append(f"{uname} – ${amt:.2f}")
    lines.append("Use /received <num>")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def clients_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    with get_cursor() as cur:
        cur.execute("""
            SELECT c.user_id, c.user_name,
                   COALESCE(SUM(o.total_price),0) AS ordered,
                   COALESCE(SUM(p.amount),0)      AS paid
            FROM clients c
            LEFT JOIN orders   o ON o.user_id = c.user_id
            LEFT JOIN payments p ON p.user_id = c.user_id
            GROUP BY c.user_id, c.user_name
            ORDER BY c.user_name;
        """)
        rows = cur.fetchall()
    lines = ["👥 **CLIENTS**"]
    total_due = 0
    for uid, uname, ordered, paid in rows:
        bal = ordered - paid
        total_due += bal
        if bal > 0:
            status = f"💳 ${bal:.2f}"
        elif bal < 0:
            status = f"💰 ${abs(bal):.2f} credit"
        else:
            status = "✅"
        lines.append(f"{uname} (ID {uid})\n{status}")
    lines.append(f"\n**Total Due: ${total_due:.2f}**")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def sales_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    with get_cursor() as cur:
        cur.execute("SELECT SUM(total_price) FROM orders")
        ordered = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(amount) FROM payments")
        paid = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(amount) FROM pending_payments")
        pending = cur.fetchone()[0] or 0
        cur.execute("""
            SELECT item_name, SUM(quantity) AS q
            FROM orders
            GROUP BY item_name
            ORDER BY q DESC
            LIMIT 10;
        """)
        items = cur.fetchall()
    bal = ordered - paid
    lines = [f"💰 **SALES**",
             f"Total Ordered: ${ordered:.2f}",
             f"Total Paid: ${paid:.2f}",
             f"Pending: ${pending:.2f}",
             f"Amount Due: ${bal:.2f}\n",
             "**Top Items:**"]
    for name, qty in items:
        lines.append(f"• {name}: {int(qty)} sold")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def balance_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    target = user.id
    if is_cm(user.id) and context.args:
        try:
            target = int(context.args[0])
        except ValueError:
            update.message.reply_text("Usage: /balance <user_id>")
            return
    elif not is_cm(user.id) and context.args:
        update.message.reply_text("You can only check your own.")
        return
    with get_cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(total_price),0),
                   COALESCE(SUM(amount),0)
            FROM orders o
            LEFT JOIN payments p ON p.user_id = o.user_id
            WHERE o.user_id = %s
        """, (target,))
        ordered, paid = cur.fetchone() or (0, 0)
    bal = ordered - paid
    emoji = "💳" if bal > 0 else ("💰" if bal < 0 else "✅")
    update.message.reply_markdown(
        f"{emoji} **BALANCE**\nOrdered: ${ordered:.2f}\nPaid: ${paid:.2f}\nDue: ${abs(bal):.2f}"
    )

# ------------------------------------------------------------------
def summary_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    target = user.id
    if is_cm(user.id) and context.args:
        try:
            target = int(context.args[0])
        except ValueError:
            update.message.reply_text("Usage: /summary <user_id>")
            return
    elif not is_cm(user.id) and context.args:
        update.message.reply_text("Only your own.")
        return
    with get_cursor() as cur:
        cur.execute("SELECT user_name FROM clients WHERE user_id=%s", (target,))
        uname = cur.fetchone()
        uname = uname[0] if uname else str(target)

        cur.execute("""
            SELECT quantity, item_name, total_price, created_at
            FROM orders
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 10
        """, (target,))
        orders = cur.fetchall()

        cur.execute("""
            SELECT amount, created_at
            FROM payments
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 10
        """, (target,))
        payments = cur.fetchall()

    lines = [f"📊 **SUMMARY – {uname}**"]
    lines.append("🍽️ **Orders**")
    tot_ord = 0
    for qty, iname, tot, ts in orders:
        tstr = ts.strftime('%m-%d') if ts else 'N/A'
        lines.append(f"• {qty}× {iname} – ${tot:.2f} _{tstr}_")
        tot_ord += tot
    lines.append(f"Total Ordered: ${tot_ord:.2f}\n")

    lines.append("💰 **Payments**")
    tot_paid = 0
    for amt, ts in payments:
        tstr = ts.strftime('%m-%d') if ts else 'N/A'
        lines.append(f"• ${amt:.2f} _{tstr}_")
        tot_paid += amt
    lines.append(f"Total Paid: ${tot_paid:.2f}\n")

    bal = tot_ord - tot_paid
    lines.append(f"💳 Amount Due: ${bal:.2f}" if bal > 0 else "✅ All Paid!")
    update.message.reply_markdown("\n".join(lines))

# ------------------------------------------------------------------
def test_notification_command(update: Update, context: CallbackContext) -> None:
    if not is_cm(update.effective_user.id):
        update.message.reply_text("Nope.")
        return
    context.bot.send_message(chat_id=CM_USER_ID, text="✅ Test ping!")
    update.message.reply_text("Sent!")

def help_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if is_cm(user.id):
        txt = ("🍽️ **MANAGER**\n"
               "/menu /orders /clients /received /pending /sales /balance <id> /summary <id> /help")
    else:
        txt = ("🍽️ **COMMANDS**\n"
               "/menu /order <item> <qty> /paid <amount> /balance /summary /help")
    update.message.reply_markdown(txt)

def error_handler(update, context):
    logger.warning("Update %s caused error %s", update, context.error)
    if update and getattr(update, 'message', None):
        update.message.reply_text("Oops, something went wrong.")

def get_handlers():
    from telegram.ext import CommandHandler
    return [
        CommandHandler("start", start_command),
        CommandHandler("menu", menu_command),
        CommandHandler("order", order_command),
        CommandHandler("paid", paid_command),
        CommandHandler("received", received_command),
        CommandHandler("pending", pending_command),
        CommandHandler("clients", clients_command),
        CommandHandler("orders", orders_command),
        CommandHandler("sales", sales_command),
        CommandHandler("test", test_notification_command),
        CommandHandler("balance", balance_command),
        CommandHandler("summary", summary_command),
        CommandHandler("help", help_command)
    ]