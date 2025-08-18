import os
import json
import logging
from flask import Flask, request
from telegram import Update, ParseMode
from telegram.ext import Application, CommandHandler, Dispatcher, CallbackContext
from telegram.utils.helpers import escape_markdown
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.exceptions import NotFound, FirebaseError
from telegram.error import TelegramError, BadRequest, Unauthorized
from datetime import datetime

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Environment ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CM_USER_ID = int(os.getenv("CM_USER_ID", 0))
PORT = int(os.getenv("PORT", 8080))
RAILWAY_URL = os.getenv("RAILWAY_URL")

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
    logger.error("Bot token is not set properly. Please check TELEGRAM_BOT_TOKEN.")
    raise ValueError("TELEGRAM_BOT_TOKEN is required.")

if CM_USER_ID == 0:
    logger.error("CM User ID is not set properly. Please check CM_USER_ID.")
    raise ValueError("CM_USER_ID is required.")

if not RAILWAY_URL:
    logger.error("RAILWAY_URL is not set.")
    raise ValueError("RAILWAY_URL environment variable is required.")

cred_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if not cred_json_str:
    logger.error("GOOGLE_APPLICATION_CREDENTIALS_JSON is not set.")
    raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON is required.")
try:
    cred_json = json.loads(cred_json_str)
except json.JSONDecodeError as e:
    logger.error(f"Invalid GOOGLE_APPLICATION_CREDENTIALS_JSON format: {e}")
    raise

# ---------- Firebase ----------
try:
    firebase_admin.initialize_app(credentials.Certificate(cred_json))
except ValueError as e:
    logger.error(f"Firebase initialization failed: {e}")
    raise
db = firestore.client()

# ---------- Helpers ----------
def md(text: str) -> str:
    return escape_markdown(str(text), version=2)

def is_cm(uid: int) -> bool:
    return uid == CM_USER_ID

def get_user_display_name(user):
    return f"@{user.username}" if user.username else user.first_name

def get_client_ref(user_id):
    return db.collection('cafeteria_clients').document(str(user_id))

def get_pending_payments_ref():
    return db.collection('pending_payments')

def notify_cm(context, message, parse_mode=ParseMode.MARKDOWN_V2):
    if not CM_USER_ID:
        logger.warning("CM_USER_ID not set")
        return
    try:
        context.bot.send_message(
            chat_id=CM_USER_ID,
            text=md(message) if parse_mode == ParseMode.MARKDOWN_V2 else message,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Failed to notify CM: {e}")

# ---------- Menu ----------
MENU = {
    'coffee':   {'name': 'Coffee',       'price': 2.50},
    'tea':      {'name': 'Tea',          'price': 2.00},
    'sandwich': {'name': 'Sandwich',     'price': 5.00},
    'burger':   {'name': 'Burger',       'price': 8.00},
    'pizza':    {'name': 'Pizza Slice',  'price': 4.50},
    'salad':    {'name': 'Salad',        'price': 6.00},
    'juice':    {'name': 'Fresh Juice',  'price': 3.50},
    'cake':     {'name': 'Cake Slice',   'price': 4.00},
}

# ---------- Handlers ----------
def start_command(update: Update, context: CallbackContext):
    user = update.effective_user
    if is_cm(user.id):
        text = ("Welcome back, Cafeteria Manager! 👨‍🍳\n\n"
                "/menu - View menu\n/orders - Recent orders\n/clients - All clients\n"
                "/received - Confirm payments\n/help - Full list")
    else:
        text = (f"Hi {md(user.first_name)}! 🍽️\n\n"
                "/menu - View items\n/order - Place order\n/paid - Report payment\n"
                "/balance - Check balance\n/help - More info")
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

def menu_command(update: Update, context: CallbackContext):
    lines = ["🍽️ **CAFETERIA MENU** 🍽️\n"]
    for k, v in MENU.items():
        lines.append(f"**{md(v['name'])}** - ${v['price']:.2f}")
    update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)

def order_command(update: Update, context: CallbackContext):
    """Allows a client to place an order. Format: /order <item_code> <quantity>"""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    if is_cm(user_id):
        update.message.reply_text("As the Cafeteria Manager, you don't need to place orders! 😄", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    if not context.args or len(context.args) < 2:
        update.message.reply_text(
            "Usage: /order <item_code> <quantity>\n"
            "Example: /order coffee 2\n\n"
            "Use /menu to see available items and their codes.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    try:
        item_code = context.args[0].lower().strip()
        if not item_code.isalnum():  # Basic sanitization
            update.message.reply_text("Invalid item code. Use only alphanumeric characters.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        quantity = int(context.args[1])
        
        if item_code not in MENU:
            available_items = ", ".join(MENU.keys())
            update.message.reply_text(
                f"Item '{md(item_code)}' not found in menu.\n"
                f"Available items: {md(available_items)}\n\n"
                f"Use /menu to see the full menu.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        if quantity <= 0:
            update.message.reply_text("Quantity must be a positive number.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        item = MENU[item_code]
        total_price = quantity * item['price']
        
        order_data = {
            'item_code': item_code,
            'item_name': item['name'],
            'quantity': quantity,
            'price_per_item': item['price'],
            'total_price': total_price,
            'user_id': user_id,
            'user_name': user_name,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'status': 'pending'
        }
        
        client_ref = get_client_ref(user_id)
        try:
            client_ref.collection('orders').add(order_data)
            client_ref.set({
                'user_name': user_name,
                'user_id': user_id,
                'last_order': firestore.SERVER_TIMESTAMP
            }, merge=True)
        except NotFound:
            logger.error(f"Firestore document not found for user_id: {user_id}")
            update.message.reply_text("Error: User data not found in the database.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        except FirebaseError as e:
            logger.error(f"Firestore error: {e}")
            update.message.reply_text("Error communicating with the database. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        update.message.reply_text(
            f"✅ Order placed successfully!\n\n"
            f"**{quantity} x {md(item['name'])}** @ ${item['price']:.2f} each\n"
            f"**Total: ${total_price:.2f}**\n\n"
            f"Your order has been sent to the cafeteria. 🍽️",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        notify_cm(
            context,
            f"🆕 **NEW ORDER**\n\n"
            f"👤 **From:** {md(user_name)}\n"
            f"🍽️ **Order:** {quantity} x {md(item['name'])}\n"
            f"💰 **Total:** ${total_price:.2f}\n\n"
            f"_Use /orders to see all recent orders_",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        logger.info(f"Order recorded for {user_name}: {order_data}")
        
    except ValueError:
        update.message.reply_text("Invalid quantity. Please use a number.\nExample: /order coffee 2", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error processing order command: {e}")
        update.message.reply_text("Sorry, there was an error processing your order. Please try again.", parse_mode=ParseMode.MARKDOWN_V2)

def paid_command(update: Update, context: CallbackContext):
    """Client reports they have made a payment. Format: /paid <amount>"""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    if is_cm(user_id):
        update.message.reply_text("Use /received to confirm payments from clients.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    if not context.args:
        update.message.reply_text("Usage: /paid <amount>\nExample: /paid 15.50", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        amount = float(context.args[0])
        
        if amount <= 0:
            update.message.reply_text("Payment amount must be positive.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        pending_data = {
            'user_id': user_id,
            'user_name': user_name,
            'amount': amount,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'status': 'pending_confirmation'
        }
        
        try:
            get_pending_payments_ref().add(pending_data)
        except FirebaseError as e:
            logger.error(f"Firestore error adding pending payment: {e}")
            update.message.reply_text("Error communicating with the database. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        update.message.reply_text(
            f"💰 Payment reported: ${amount:.2f}\n\n"
            f"Your payment is pending confirmation from the cafeteria manager. "
            f"You'll be notified once it's confirmed. ⏳",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        notify_cm(
            context,
            f"💰 **PAYMENT REPORTED**\n\n"
            f"👤 **From:** {md(user_name)}\n"
            f"💵 **Amount:** ${amount:.2f}\n\n"
            f"_Use /received to confirm this payment_",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        logger.info(f"Payment reported by {user_name}: ${amount:.2f}")
        
    except ValueError:
        update.message.reply_text("Invalid amount. Please use a number.\nExample: /paid 15.50", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error processing paid command: {e}")
        update.message.reply_text("Sorry, there was an error reporting your payment. Please try again.", parse_mode=ParseMode.MARKDOWN_V2)

def orders_command(update: Update, context: CallbackContext) -> None:
    """CM views recent orders from all clients."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all orders.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        clients_collection = db.collection('cafeteria_clients')
        clients = []
        
        try:
            for client_doc in clients_collection.stream():
                try:
                    client_data = client_doc.to_dict()
                    if client_data:
                        clients.append((client_doc.id, client_data))
                except Exception as e:
                    logger.warning(f"Skipping corrupted client document {client_doc.id}: {e}")
                    continue
        except FirebaseError as e:
            logger.error(f"Error streaming clients collection: {e}")
            update.message.reply_text("Error accessing client data. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        if not clients:
            update.message.reply_text("No clients found yet.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        message = "📋 **RECENT ORDERS** 📋\n\n"
        all_orders = []
        
        for client_id, client_data in clients:
            try:
                user_id_from_data = client_data.get('user_id')
                client_name = client_data.get('user_name', f'User {user_id_from_data or client_id}')
                actual_client_id = user_id_from_data or client_id
                
                try:
                    client_ref = get_client_ref(actual_client_id)
                    orders_query = client_ref.collection('orders').limit(5)
                    orders = list(orders_query.stream())
                    
                    for order_doc in orders:
                        try:
                            order = order_doc.to_dict()
                            if order:
                                order['client_name'] = client_name
                                order['client_id'] = actual_client_id
                                all_orders.append(order)
                        except Exception as e:
                            logger.warning(f"Skipping corrupted order for client {client_name}: {e}")
                            continue
                except FirebaseError as e:
                    logger.warning(f"Error getting orders for client {client_name}: {e}")
                    continue
                    
            except Exception as e:
                logger.warning(f"Error processing client data: {e}")
                continue
        
        if not all_orders:
            update.message.reply_text("No orders found.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        try:
            all_orders.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)
        except Exception as e:
            logger.warning(f"Error sorting orders: {e}")
        
        order_count = 0
        for order in all_orders[:20]:
            try:
                timestamp = order.get('timestamp')
                time_str = timestamp.strftime('%m-%d %H:%M') if timestamp else 'N/A'
                client_name = order.get('client_name', 'Unknown')
                quantity = order.get('quantity', 0)
                item_name = order.get('item_name', 'Unknown')
                total_price = order.get('total_price', 0)
                
                message += f"**{md(client_name)}** _{time_str}_\n"
                message += f"{quantity}x {md(item_name)} - ${total_price:.2f}\n\n"
                order_count += 1
                
            except Exception as e:
                logger.warning(f"Error formatting order: {e}")
                continue
        
        if order_count == 0:
            update.message.reply_text("No valid orders found.", parse_mode=ParseMode.MARKDOWN_V2)
            return
            
        if len(all_orders) > 20:
            message += f"... and {len(all_orders) - 20} more orders"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        update.message.reply_text("Error retrieving orders. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)

def test_notification_command(update: Update, context: CallbackContext) -> None:
    """Test command for CM to verify notifications work."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can test notifications.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        context.bot.send_message(
            chat_id=CM_USER_ID,
            text="✅ **NOTIFICATION TEST**\n\nIf you see this message, notifications are working correctly!",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        update.message.reply_text("Test notification sent! Check if you received it.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Test notification failed: {e}")
        update.message.reply_text(f"❌ Notification test failed: {e}", parse_mode=ParseMode.MARKDOWN_V2)

def pending_command(update: Update, context: CallbackContext) -> None:
    """CM views all pending payments."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view pending payments.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        pending_payments = list(get_pending_payments_ref()
                              .where('status', '==', 'pending_confirmation')
                              .stream())
        
        if not pending_payments:
            update.message.reply_text("✅ No pending payments!", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        pending_list = []
        for doc in pending_payments:
            try:
                payment = doc.to_dict()
                if payment:
                    pending_list.append(payment)
            except Exception as e:
                logger.warning(f"Skipping corrupted payment document: {e}")
                continue
        
        pending_list.sort(key=lambda x: x.get('timestamp', datetime.min))
        
        message = "💰 **PENDING PAYMENTS** 💰\n\n"
        total_pending = 0
        
        for i, payment in enumerate(pending_list, 1):
            timestamp = payment.get('timestamp')
            time_str = timestamp.strftime('%m-%d %H:%M') if timestamp else 'N/A'
            user_name = payment.get('user_name', 'Unknown User')
            amount = payment.get('amount', 0)
            
            message += f"{i}. **{md(user_name)}** - ${amount:.2f} _{time_str}_\n"
            total_pending += amount
        
        message += f"\n**Total Pending: ${total_pending:.2f}**\n\n"
        message += f"Use `/received <number>` to confirm payments"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except FirebaseError as e:
        logger.error(f"Error getting pending payments: {e}")
        update.message.reply_text("Error retrieving pending payments. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)

def clients_command(update: Update, context: CallbackContext) -> None:
    """CM views all clients and their balances."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all clients.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        clients_collection = db.collection('cafeteria_clients')
        clients = []
        
        try:
            for client_doc in clients_collection.stream():
                try:
                    client_data = client_doc.to_dict()
                    if client_data:
                        clients.append((client_doc.id, client_data))
                except Exception as e:
                    logger.warning(f"Skipping corrupted client document {client_doc.id}: {e}")
                    continue
        except FirebaseError as e:
            logger.error(f"Error streaming clients collection: {e}")
            update.message.reply_text("Error accessing client data. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        if not clients:
            update.message.reply_text("No clients found yet.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        message = "👥 **ALL CLIENTS** 👥\n\n"
        total_due = 0
        processed_clients = 0
        
        for client_id, client_data in clients:
            try:
                user_id_from_data = client_data.get('user_id')
                client_name = client_data.get('user_name', f'User {user_id_from_data or client_id}')
                actual_client_id = user_id_from_data or client_id
                
                client_ref = get_client_ref(actual_client_id)
                
                total_ordered = 0
                try:
                    orders = list(client_ref.collection('orders').stream())
                    for order_doc in orders:
                        try:
                            order_data = order_doc.to_dict()
                            if order_data:
                                total_ordered += order_data.get('total_price', 0)
                        except Exception as e:
                            logger.warning(f"Skipping corrupted order for client {client_name}: {e}")
                            continue
                except FirebaseError as e:
                    logger.warning(f"Error getting orders for client {client_name}: {e}")
                
                total_paid = 0
                try:
                    payments = list(client_ref.collection('payments').stream())
                    for payment_doc in payments:
                        try:
                            payment_data = payment_doc.to_dict()
                            if payment_data:
                                total_paid += payment_data.get('amount', 0)
                        except Exception as e:
                            logger.warning(f"Skipping corrupted payment for client {client_name}: {e}")
                            continue
                except FirebaseError as e:
                    logger.warning(f"Error getting payments for client {client_name}: {e}")
                
                balance = total_ordered - total_paid
                
                if balance > 0:
                    status = f"💳 ${balance:.2f}"
                    total_due += balance
                elif balance < 0:
                    status = f"💰 ${abs(balance):.2f} credit"
                else:
                    status = "✅ Paid"
                
                message += f"**{md(client_name)}** (ID: {actual_client_id})\n{status}\n\n"
                processed_clients += 1
                
            except Exception as e:
                logger.warning(f"Error processing client data: {e}")
                continue
        
        if processed_clients == 0:
            update.message.reply_text("No valid client data found.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        message += f"**Total Amount Due: ${total_due:.2f}**\n\n"
        message += "Use `/balance <user_id>` or `/summary <user_id>` for details"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Error getting clients list: {e}")
        update.message.reply_text("Error retrieving clients list. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)

def received_command(update: Update, context: CallbackContext) -> None:
    """CM confirms receipt of payment."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can confirm payments.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        pending_payments_query = get_pending_payments_ref().where('status', '==', 'pending_confirmation')
        pending_payments = list(pending_payments_query.limit(10).stream())
        
        pending_list = []
        for doc in pending_payments:
            try:
                payment_data = doc.to_dict()
                if payment_data:
                    pending_list.append((doc, payment_data))
            except Exception as e:
                logger.warning(f"Skipping corrupted pending payment: {e}")
                continue
        
        pending_list.sort(key=lambda x: x[1].get('timestamp', datetime.min))
        
        if not pending_list:
            update.message.reply_text("No pending payments to confirm.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        if not context.args:
            message = "💰 **PENDING PAYMENTS** 💰\n\n"
            for i, (doc, payment) in enumerate(pending_list, 1):
                message += f"{i}. {md(payment['user_name'])} - ${payment['amount']:.2f}\n"
            
            message += f"\nUse `/received <number>` to confirm a payment\n"
            message += f"Example: `/received 1` to confirm the first payment"
            
            update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        payment_index = int(context.args[0]) - 1
        
        if payment_index < 0 or payment_index >= len(pending_list):
            update.message.reply_text(f"Invalid payment number. Use /received to see pending payments.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        payment_doc, payment_data = pending_list[payment_index]
        
        client_ref = get_client_ref(payment_data['user_id'])
        try:
            client_ref.collection('payments').add({
                'amount': payment_data['amount'],
                'user_id': payment_data['user_id'],
                'user_name': payment_data['user_name'],
                'confirmed_by_cm': True,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'original_timestamp': payment_data['timestamp']
            })
            payment_doc.reference.delete()
        except FirebaseError as e:
            logger.error(f"Firestore error confirming payment: {e}")
            update.message.reply_text("Error confirming payment. Please try again later.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        update.message.reply_text(
            f"✅ Payment confirmed!\n\n"
            f"**${payment_data['amount']:.2f}** from **{md(payment_data['user_name'])}**",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        try:
            context.bot.send_message(
                chat_id=payment_data['user_id'],
                text=f"✅ Your payment of ${payment_data['amount']:.2f} has been confirmed! 🎉",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to notify client about confirmed payment: {e}")
        
        logger.info(f"CM confirmed payment: ${payment_data['amount']:.2f} from {payment_data['user_name']}")
        
    except (ValueError, IndexError):
        update.message.reply_text("Usage: /received <payment_number>\nUse /received to see pending payments.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error processing received command: {e}")
        update.message.reply_text("Sorry, there was an error confirming the payment. Please try again.", parse_mode=ParseMode.MARKDOWN_V2)

def sales_command(update: Update, context: CallbackContext) -> None:
    """CM views sales summary."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view sales summary.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        clients = list(db.collection('cafeteria_clients').stream())
        
        if not clients:
            update.message.reply_text("No sales data available.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        message = "💰 **SALES SUMMARY** 💰\n\n"
        
        total_ordered = 0
        total_paid = 0
        total_pending = 0
        item_sales = {}
        
        for client_doc in clients:
            client_data = client_doc.to_dict()
            client_id = client_data.get('user_id')
            client_ref = get_client_ref(client_id)
            
            try:
                orders = list(client_ref.collection('orders').stream())
                for order_doc in orders:
                    order = order_doc.to_dict()
                    order_total = order.get('total_price', 0)
                    total_ordered += order_total
                    
                    item_name = order.get('item_name', 'Unknown')
                    quantity = order.get('quantity', 0)
                    if item_name in item_sales:
                        item_sales[item_name] += quantity
                    else:
                        item_sales[item_name] = quantity
            except FirebaseError as e:
                logger.warning(f"Error getting orders for client {client_id}: {e}")
                continue
            
            try:
                payments = list(client_ref.collection('payments').stream())
                for payment_doc in payments:
                    payment = payment_doc.to_dict()
                    total_paid += payment.get('amount', 0)
            except FirebaseError as e:
                logger.warning(f"Error getting payments for client {client_id}: {e}")
                continue
        
        try:
            pending_payments = list(get_pending_payments_ref()
                                  .where('status', '==', 'pending_confirmation')
                                  .stream())
            for pending_doc in pending_payments:
                pending = pending_doc.to_dict()
                total_pending += pending.get('amount', 0)
        except FirebaseError as e:
            logger.warning(f"Error getting pending payments: {e}")
        
        balance_due = total_ordered - total_paid
        
        message += f"**Total Orders:** ${total_ordered:.2f}\n"
        message += f"**Total Paid:** ${total_paid:.2f}\n"
        message += f"**Pending Payments:** ${total_pending:.2f}\n"
        message += f"**Amount Due:** ${balance_due:.2f}\n\n"
        
        message += "📊 **TOP SELLING ITEMS** 📊\n"
        sorted_items = sorted(item_sales.items(), key=lambda x: x[1], reverse=True)
        for item, quantity in sorted_items[:10]:
            message += f"• **{md(item)}:** {quantity} sold\n"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Error getting sales summary: {e}")
        update.message.reply_text("Error retrieving sales summary.", parse_mode=ParseMode.MARKDOWN_V2)

def balance_command(update: Update, context: CallbackContext) -> None:
    """Shows balance for user or specified client (CM only)."""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    target_user_id = user_id
    target_user_name = user_name
    
    if is_cm(user_id) and context.args:
        try:
            target_user_id = int(context.args[0])
            client_doc = get_client_ref(target_user_id).get()
            if client_doc.exists:
                target_user_name = client_doc.to_dict().get('user_name', f'User {target_user_id}')
            else:
                target_user_name = f'User {target_user_id}'
        except ValueError:
            update.message.reply_text("Usage: /balance <user_id>\nExample: /balance 12345", parse_mode=ParseMode.MARKDOWN_V2)
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own balance. Use /balance without arguments.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        client_ref = get_client_ref(target_user_id)
        
        orders = client_ref.collection('orders').stream()
        total_ordered = sum(order.to_dict().get('total_price', 0) for order in orders)
        
        payments = client_ref.collection('payments').stream()
        total_paid = sum(payment.to_dict().get('amount', 0) for payment in payments)
        
        balance = total_ordered - total_paid
        
        if balance > 0:
            status_emoji = "💳"
            status_text = "Amount Due"
        elif balance < 0:
            status_emoji = "💰"
            status_text = "Credit Balance"
        else:
            status_emoji = "✅"
            status_text = "All Paid Up"
        
        message = f"{status_emoji} **BALANCE - {md(target_user_name)}** {status_emoji}\n\n"
        message += f"Total Ordered: ${total_ordered:.2f}\n"
        message += f"Total Paid: ${total_paid:.2f}\n"
        message += f"**{status_text}: ${abs(balance):.2f}**"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except FirebaseError as e:
        logger.error(f"Error calculating balance for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve balance for {md(target_user_name)}.", parse_mode=ParseMode.MARKDOWN_V2)

def summary_command(update: Update, context: CallbackContext) -> None:
    """Shows order and payment summary."""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    target_user_id = user_id
    target_user_name = user_name
    
    if is_cm(user_id) and context.args:
        try:
            target_user_id = int(context.args[0])
            client_doc = get_client_ref(target_user_id).get()
            if client_doc.exists:
                target_user_name = client_doc.to_dict().get('user_name', f'User {target_user_id}')
            else:
                target_user_name = f'User {target_user_id}'
        except ValueError:
            update.message.reply_text("Usage: /summary <user_id>\nExample: /summary 12345", parse_mode=ParseMode.MARKDOWN_V2)
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own summary. Use /summary without arguments.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    try:
        client_ref = get_client_ref(target_user_id)
        
        message = f"📊 **SUMMARY - {md(target_user_name)}** 📊\n\n"
        
        message += "🍽️ **RECENT ORDERS**\n"
        orders = list(client_ref.collection('orders')
                     .order_by('timestamp', direction=firestore.Query.DESCENDING)
                     .limit(10).stream())
        
        total_ordered = 0
        if orders:
            for doc in orders:
                order = doc.to_dict()
                timestamp = order.get('timestamp')
                time_str = timestamp.strftime('%m-%d') if timestamp else 'N/A'
                message += f"• {order.get('quantity')}x {md(order.get('item_name'))} - ${order.get('total_price', 0):.2f} _{time_str}_\n"
                total_ordered += order.get('total_price', 0)
        else:
            message += "No orders found.\n"
        
        message += f"\n**Total Ordered: ${total_ordered:.2f}**\n\n"
        
        message += "💰 **RECENT PAYMENTS**\n"
        payments = list(client_ref.collection('payments')
                       .order_by('timestamp', direction=firestore.Query.DESCENDING)
                       .limit(10).stream())
        
        total_paid = 0
        if payments:
            for doc in payments:
                payment = doc.to_dict()
                timestamp = payment.get('timestamp')
                time_str = timestamp.strftime('%m-%d') if timestamp else 'N/A'
                message += f"• ${payment.get('amount', 0):.2f} _{time_str}_\n"
                total_paid += payment.get('amount', 0)
        else:
            message += "No payments found.\n"
        
        message += f"\n**Total Paid: ${total_paid:.2f}**\n\n"
        
        balance = total_ordered - total_paid
        if balance > 0:
            message += f"💳 **Amount Due: ${balance:.2f}**"
        elif balance < 0:
            message += f"💰 **Credit: ${abs(balance):.2f}**"
        else:
            message += f"✅ **All Paid Up!**"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
        
    except FirebaseError as e:
        logger.error(f"Error generating summary for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve summary for {md(target_user_name)}.", parse_mode=ParseMode.MARKDOWN_V2)

def help_command(update: Update, context: CallbackContext) -> None:
    """Shows help information."""
    user_id = update.message.from_user.id
    
    if is_cm(user_id):
        help_text = (
            "🍽️ **CAFETERIA MANAGER COMMANDS** 🍽️\n\n"
            "**📋 ORDER MANAGEMENT**\n"
            "/menu - View menu items\n"
            "/orders - View recent orders from all clients\n"
            "/clients - View all clients and their balances\n\n"
            "**💰 PAYMENT MANAGEMENT**\n"
            "/received - Confirm payments from clients\n"
            "/pending - View all pending payments\n"
            "/sales - View sales summary\n\n"
            "**👤 CLIENT INFO**\n"
            "/balance <user_id> - Check any client's balance\n"
            "/summary <user_id> - View client's order history\n\n"
            "/help - Show this help message\n\n"
            "💡 You'll receive notifications for new orders and payment reports."
        )
    else:
        help_text = (
            "🍽️ **CAFETERIA BOT COMMANDS** 🍽️\n\n"
            "/menu - View available food items\n"
            "/order <item> <quantity> - Place an order\n"
            "/paid <amount> - Report payment made\n"
            "/balance - Check your current balance\n"
            "/summary - View your order & payment history\n"
            "/help - Show this help message\n\n"
            "📝 **Example:** /order coffee 2\n"
            "💰 **Example:** /paid 15.50"
        )
    
    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

def error_handler(update: object, context: CallbackContext) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    if isinstance(context.error, BadRequest):
        message = "Invalid request sent to Telegram. Please check your input."
    elif isinstance(context.error, Unauthorized):
        message = "Bot lacks permission to perform this action."
    else:
        message = "An unexpected error occurred. Please try again later."
    if update and hasattr(update, 'message') and update.message:
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)

# ---------- Flask App ----------
app = Flask(__name__)
updater = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Register handlers
updater.add_handler(CommandHandler("start", start_command))
updater.add_handler(CommandHandler("menu", menu_command))
updater.add_handler(CommandHandler("order", order_command))
updater.add_handler(CommandHandler("paid", paid_command))
updater.add_handler(CommandHandler("received", received_command))
updater.add_handler(CommandHandler("pending", pending_command))
updater.add_handler(CommandHandler("clients", clients_command))
updater.add_handler(CommandHandler("orders", orders_command))
updater.add_handler(CommandHandler("sales", sales_command))
updater.add_handler(CommandHandler("balance", balance_command))
updater.add_handler(CommandHandler("summary", summary_command))
updater.add_handler(CommandHandler("help", help_command))
updater.add_handler(CommandHandler("test_notification", test_notification_command))
updater.add_error_handler(error_handler)

@app.route("/", methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), updater.bot)
        updater.dispatcher.process_update(update)
        return "", 200
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}")
        return "", 500

def main() -> None:
    """Start the bot."""
    try:
        updater.bot.set_webhook(f"{RAILWAY_URL}/")
        logger.info(f"Webhook set to {RAILWAY_URL}/")
    except TelegramError as e:
        logger.error(f"Failed to set webhook: {e}")
        raise
    
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()