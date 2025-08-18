import os
import json
import logging
from flask import Flask, request
from telegram import Update
####from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Environment ----------
TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
CM_USER_ID  = int(os.getenv("CM_USER_ID", 0))
PORT        = int(os.getenv("PORT", 8080))

# ---------- Firebase ----------
try:
    cred_json = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
    firebase_admin.initialize_app(credentials.Certificate(cred_json))
    db = firestore.client()
except Exception as e:
    logger.error(f"Firebase initialization failed: {e}")
    raise

# ---------- Helpers ----------
def md(text: str) -> str:
    """Simple markdown escaping - avoiding deprecated escape_markdown"""
    if not text:
        return ""
    # Basic escaping for Markdown V2
    text = str(text)
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    return text

def is_cm(uid: int) -> bool:
    return uid == CM_USER_ID

def get_user_display_name(user):
    if not user:
        return "Unknown User"
    return f"@{user.username}" if user.username else user.first_name

def get_client_ref(user_id):
    return db.collection('cafeteria_clients').document(str(user_id))

def get_pending_payments_ref():
    return db.collection('pending_payments')

def notify_cm(context, message, use_markdown=True):
    if not CM_USER_ID:
        logger.warning("CM_USER_ID not set")
        return
    try:
        context.bot.send_message(
            chat_id=CM_USER_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN if use_markdown else None
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
        text = (f"Hi {user.first_name}! 🍽️\n\n"
                "/menu - View items\n/order - Place order\n/paid - Report payment\n"
                "/balance - Check balance\n/help - More info")
    update.message.reply_text(text)

def menu_command(update: Update, context: CallbackContext):
    lines = ["🍽️ **CAFETERIA MENU** 🍽️\n"]
    for k, v in MENU.items():
        lines.append(f"**{v['name']}** - ${v['price']:.2f}")
    update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

def order_command(update: Update, context: CallbackContext):
    """Allows a client to place an order. Format: /order <item_code> <quantity>"""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    if is_cm(user_id):
        update.message.reply_text("As the Cafeteria Manager, you don't need to place orders! 😄")
        return
    
    if not context.args or len(context.args) < 2:
        update.message.reply_text(
            "Usage: /order <item_code> <quantity>\n"
            "Example: /order coffee 2\n\n"
            "Use /menu to see available items and their codes."
        )
        return
    
    try:
        item_code = context.args[0].lower()
        quantity = int(context.args[1])
        
        if item_code not in MENU:
            available_items = ", ".join(MENU.keys())
            update.message.reply_text(
                f"Item '{item_code}' not found in menu.\n"
                f"Available items: {available_items}\n\n"
                f"Use /menu to see the full menu."
            )
            return
        
        if quantity <= 0:
            update.message.reply_text("Quantity must be a positive number.")
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
        client_ref.collection('orders').add(order_data)
        
        # Also store user info for CM reference
        client_ref.set({
            'user_name': user_name,
            'user_id': user_id,
            'last_order': firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        update.message.reply_text(
            f"✅ Order placed successfully!\n\n"
            f"**{quantity} x {item['name']}** @ ${item['price']:.2f} each\n"
            f"**Total: ${total_price:.2f}**\n\n"
            f"Your order has been sent to the cafeteria. 🍽️",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify CM about new order
        notify_cm(
            context,
            f"🆕 **NEW ORDER**\n\n"
            f"👤 **From:** {user_name}\n"
            f"🍽️ **Order:** {quantity} x {item['name']}\n"
            f"💰 **Total:** ${total_price:.2f}\n\n"
            f"_Use /orders to see all recent orders_"
        )
        
        logger.info(f"Order recorded for {user_name}: {order_data}")
        
    except ValueError:
        update.message.reply_text("Invalid quantity. Please use a number.\nExample: /order coffee 2")
    except Exception as e:
        logger.error(f"Error processing order command: {e}")
        update.message.reply_text("Sorry, there was an error processing your order. Please try again.")

def paid_command(update: Update, context: CallbackContext):
    """Client reports they have made a payment. Format: /paid <amount>"""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    if is_cm(user_id):
        update.message.reply_text("Use /received to confirm payments from clients.")
        return
    
    if not context.args:
        update.message.reply_text("Usage: /paid <amount>\nExample: /paid 15.50")
        return
    
    try:
        amount = float(context.args[0])
        
        if amount <= 0:
            update.message.reply_text("Payment amount must be positive.")
            return
        
        # Add to pending payments for CM to confirm
        pending_data = {
            'user_id': user_id,
            'user_name': user_name,
            'amount': amount,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'status': 'pending_confirmation'
        }
        
        get_pending_payments_ref().add(pending_data)
        
        update.message.reply_text(
            f"💰 Payment reported: ${amount:.2f}\n\n"
            f"Your payment is pending confirmation from the cafeteria manager. "
            f"You'll be notified once it's confirmed. ⏳"
        )
        
        # Notify CM about payment claim
        notify_cm(
            context,
            f"💰 **PAYMENT REPORTED**\n\n"
            f"👤 **From:** {user_name}\n"
            f"💵 **Amount:** ${amount:.2f}\n\n"
            f"_Use /received to confirm this payment_"
        )
        
        logger.info(f"Payment reported by {user_name}: ${amount:.2f}")
        
    except ValueError:
        update.message.reply_text("Invalid amount. Please use a number.\nExample: /paid 15.50")
    except Exception as e:
        logger.error(f"Error processing paid command: {e}")
        update.message.reply_text("Sorry, there was an error reporting your payment. Please try again.")

def orders_command(update: Update, context: CallbackContext):
    """CM views recent orders from all clients."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all orders.")
        return
    
    try:
        # Get all clients with error handling for corrupted documents
        clients_collection = db.collection('cafeteria_clients')
        clients = []
        
        try:
            for client_doc in clients_collection.stream():
                try:
                    client_data = client_doc.to_dict()
                    if client_data:  # Only add if data exists
                        clients.append((client_doc.id, client_data))
                except Exception as e:
                    logger.warning(f"Skipping corrupted client document {client_doc.id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error streaming clients collection: {e}")
            update.message.reply_text("Error accessing client data. Please try again later.")
            return
        
        if not clients:
            update.message.reply_text("No clients found yet.")
            return
        
        message = "📋 **RECENT ORDERS** 📋\n\n"
        all_orders = []
        
        for client_id, client_data in clients:
            try:
                user_id_from_data = client_data.get('user_id')
                client_name = client_data.get('user_name', f'User {user_id_from_data or client_id}')
                
                # Use the actual user_id from the data, fallback to document ID
                actual_client_id = user_id_from_data or client_id
                
                # Get recent orders for this client with error handling
                try:
                    client_ref = get_client_ref(actual_client_id)
                    orders_query = client_ref.collection('orders').limit(5)
                    orders = list(orders_query.stream())
                    
                    for order_doc in orders:
                        try:
                            order = order_doc.to_dict()
                            if order:  # Check if order data exists
                                order['client_name'] = client_name
                                order['client_id'] = actual_client_id
                                all_orders.append(order)
                        except Exception as e:
                            logger.warning(f"Skipping corrupted order for client {client_name}: {e}")
                            continue
                            
                except Exception as e:
                    logger.warning(f"Error getting orders for client {client_name}: {e}")
                    continue
                    
            except Exception as e:
                logger.warning(f"Error processing client data: {e}")
                continue
        
        if not all_orders:
            update.message.reply_text("No orders found.")
            return
        
        # Sort all orders by timestamp in Python (most recent first)
        try:
            all_orders.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)
        except Exception as e:
            logger.warning(f"Error sorting orders: {e}")
        
        # Show latest 20 orders
        order_count = 0
        for order in all_orders[:20]:
            try:
                timestamp = order.get('timestamp')
                time_str = timestamp.strftime('%m-%d %H:%M') if timestamp else 'N/A'
                client_name = order.get('client_name', 'Unknown')
                quantity = order.get('quantity', 0)
                item_name = order.get('item_name', 'Unknown')
                total_price = order.get('total_price', 0)
                
                message += f"**{client_name}** _{time_str}_\n"
                message += f"{quantity}x {item_name} - ${total_price:.2f}\n\n"
                order_count += 1
                
            except Exception as e:
                logger.warning(f"Error formatting order: {e}")
                continue
        
        if order_count == 0:
            update.message.reply_text("No valid orders found.")
            return
            
        if len(all_orders) > 20:
            message += f"... and {len(all_orders) - 20} more orders"
        
        # Split long messages
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        update.message.reply_text("Error retrieving orders. Please try again later.")

def pending_command(update: Update, context: CallbackContext):
    """CM views all pending payments."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view pending payments.")
        return
    
    try:
        # Simplified query - remove order_by to avoid index requirement
        pending_payments = list(get_pending_payments_ref()
                              .where('status', '==', 'pending_confirmation')
                              .stream())
        
        if not pending_payments:
            update.message.reply_text("✅ No pending payments!")
            return
        
        # Sort in Python instead of Firestore to avoid index requirement
        pending_list = []
        for doc in pending_payments:
            try:
                payment = doc.to_dict()
                if payment:  # Check if payment data exists
                    pending_list.append(payment)
            except Exception as e:
                logger.warning(f"Skipping corrupted payment document: {e}")
                continue
        
        # Sort by timestamp in Python
        pending_list.sort(key=lambda x: x.get('timestamp', datetime.min))
        
        message = "💰 **PENDING PAYMENTS** 💰\n\n"
        total_pending = 0
        
        for i, payment in enumerate(pending_list, 1):
            timestamp = payment.get('timestamp')
            time_str = timestamp.strftime('%m-%d %H:%M') if timestamp else 'N/A'
            user_name = payment.get('user_name', 'Unknown User')
            amount = payment.get('amount', 0)
            
            message += f"{i}. **{user_name}** - ${amount:.2f} _{time_str}_\n"
            total_pending += amount
        
        message += f"\n**Total Pending: ${total_pending:.2f}**\n\n"
        message += "Use `/received <number>` to confirm payments"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error getting pending payments: {e}")
        update.message.reply_text("Error retrieving pending payments. Please try again later.")

def clients_command(update: Update, context: CallbackContext):
    """CM views all clients and their balances."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all clients.")
        return
    
    try:
        # Get all clients with error handling for corrupted documents
        clients_collection = db.collection('cafeteria_clients')
        clients = []
        
        try:
            for client_doc in clients_collection.stream():
                try:
                    client_data = client_doc.to_dict()
                    if client_data:  # Only add if data exists
                        clients.append((client_doc.id, client_data))
                except Exception as e:
                    logger.warning(f"Skipping corrupted client document {client_doc.id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error streaming clients collection: {e}")
            update.message.reply_text("Error accessing client data. Please try again later.")
            return
        
        if not clients:
            update.message.reply_text("No clients found yet.")
            return
        
        message = "👥 **ALL CLIENTS** 👥\n\n"
        total_due = 0
        processed_clients = 0
        
        for client_id, client_data in clients:
            try:
                user_id_from_data = client_data.get('user_id')
                client_name = client_data.get('user_name', f'User {user_id_from_data or client_id}')
                
                # Use the actual user_id from the data, fallback to document ID
                actual_client_id = user_id_from_data or client_id
                
                # Calculate balance for this client with error handling
                try:
                    client_ref = get_client_ref(actual_client_id)
                    
                    # Get orders with error handling
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
                    except Exception as e:
                        logger.warning(f"Error getting orders for client {client_name}: {e}")
                    
                    # Get payments with error handling
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
                    except Exception as e:
                        logger.warning(f"Error getting payments for client {client_name}: {e}")
                    
                    balance = total_ordered - total_paid
                    
                    if balance > 0:
                        status = f"💳 ${balance:.2f}"
                        total_due += balance
                    elif balance < 0:
                        status = f"💰 ${abs(balance):.2f} credit"
                    else:
                        status = "✅ Paid"
                    
                    message += f"**{client_name}** (ID: {actual_client_id})\n{status}\n\n"
                    processed_clients += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing client {client_name}: {e}")
                    message += f"**{client_name}** - Error calculating balance\n\n"
                    continue
                
            except Exception as e:
                logger.warning(f"Error processing client data: {e}")
                continue
        
        if processed_clients == 0:
            update.message.reply_text("No valid client data found.")
            return
        
        message += f"**Total Amount Due: ${total_due:.2f}**\n\n"
        message += "Use `/balance <user_id>` or `/summary <user_id>` for details"
        
        # Split long messages
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error getting clients list: {e}")
        update.message.reply_text("Error retrieving clients list. Please try again later.")

def received_command(update: Update, context: CallbackContext):
    """CM confirms receipt of payment."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can confirm payments.")
        return
    
    try:
        # Get pending payments without order_by to avoid index requirement
        pending_payments_query = get_pending_payments_ref().where('status', '==', 'pending_confirmation')
        pending_payments = list(pending_payments_query.limit(10).stream())
        
        # Sort in Python instead of Firestore
        pending_list = []
        for doc in pending_payments:
            try:
                payment_data = doc.to_dict()
                if payment_data:
                    pending_list.append((doc, payment_data))
            except Exception as e:
                logger.warning(f"Skipping corrupted pending payment: {e}")
                continue
        
        # Sort by timestamp
        pending_list.sort(key=lambda x: x[1].get('timestamp', datetime.min))
        
        if not pending_list:
            update.message.reply_text("No pending payments to confirm.")
            return
        
        # Show pending payments for confirmation
        if not context.args:
            message = "💰 **PENDING PAYMENTS** 💰\n\n"
            for i, (doc, payment) in enumerate(pending_list, 1):
                message += f"{i}. {payment['user_name']} - ${payment['amount']:.2f}\n"
            
            message += "\nUse `/received <number>` to confirm a payment\n"
            message += "Example: `/received 1` to confirm the first payment"
            
            update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
            return
        
        # Confirm specific payment
        payment_index = int(context.args[0]) - 1
        
        if payment_index < 0 or payment_index >= len(pending_list):
            update.message.reply_text("Invalid payment number. Use /received to see pending payments.")
            return
        
        payment_doc, payment_data = pending_list[payment_index]
        
        # Move to confirmed payments
        client_ref = get_client_ref(payment_data['user_id'])
        client_ref.collection('payments').add({
            'amount': payment_data['amount'],
            'user_id': payment_data['user_id'],
            'user_name': payment_data['user_name'],
            'confirmed_by_cm': True,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'original_timestamp': payment_data['timestamp']
        })
        
        # Remove from pending
        payment_doc.reference.delete()
        
        update.message.reply_text(
            f"✅ Payment confirmed!\n\n"
            f"**${payment_data['amount']:.2f}** from **{payment_data['user_name']}**",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Notify client
        try:
            context.bot.send_message(
                chat_id=payment_data['user_id'],
                text=f"✅ Your payment of ${payment_data['amount']:.2f} has been confirmed! 🎉"
            )
        except Exception as e:
            logger.error(f"Failed to notify client about confirmed payment: {e}")
        
        logger.info(f"CM confirmed payment: ${payment_data['amount']:.2f} from {payment_data['user_name']}")
        
    except (ValueError, IndexError):
        update.message.reply_text("Usage: /received <payment_number>\nUse /received to see pending payments.")
    except Exception as e:
        logger.error(f"Error processing received command: {e}")
        update.message.reply_text("Sorry, there was an error confirming the payment. Please try again.")

def sales_command(update: Update, context: CallbackContext):
    """CM views sales summary."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view sales summary.")
        return
    
    try:
        clients = list(db.collection('cafeteria_clients').stream())
        
        if not clients:
            update.message.reply_text("No sales data available.")
            return
        
        message = "💰 **SALES SUMMARY** 💰\n\n"
        
        total_ordered = 0
        total_paid = 0
        total_pending = 0
        item_sales = {}
        
        for client_doc in clients:
            try:
                client_data = client_doc.to_dict()
                if not client_data:
                    continue
                
                client_id = client_data.get('user_id')
                if not client_id:
                    continue
                    
                client_ref = get_client_ref(client_id)
                
                # Get orders
                orders = list(client_ref.collection('orders').stream())
                for order_doc in orders:
                    try:
                        order = order_doc.to_dict()
                        if not order:
                            continue
                            
                        order_total = order.get('total_price', 0)
                        total_ordered += order_total
                        
                        # Track item sales
                        item_name = order.get('item_name', 'Unknown')
                        quantity = order.get('quantity', 0)
                        if item_name in item_sales:
                            item_sales[item_name] += quantity
                        else:
                            item_sales[item_name] = quantity
                    except Exception as e:
                        logger.warning(f"Error processing order: {e}")
                        continue
                
                # Get payments
                payments = list(client_ref.collection('payments').stream())
                for payment_doc in payments:
                    try:
                        payment = payment_doc.to_dict()
                        if payment:
                            total_paid += payment.get('amount', 0)
                    except Exception as e:
                        logger.warning(f"Error processing payment: {e}")
                        continue
            except Exception as e:
                logger.warning(f"Error processing client: {e}")
                continue
        
        # Get pending payments
        try:
            pending_payments = list(get_pending_payments_ref()
                                  .where('status', '==', 'pending_confirmation')
                                  .stream())
            for pending_doc in pending_payments:
                try:
                    pending = pending_doc.to_dict()
                    if pending:
                        total_pending += pending.get('amount', 0)
                except Exception as e:
                    logger.warning(f"Error processing pending payment: {e}")
                    continue
        except Exception as e:
            logger.warning(f"Error getting pending payments: {e}")
        
        balance_due = total_ordered - total_paid
        
        message += f"**Total Orders:** ${total_ordered:.2f}\n"
        message += f"**Total Paid:** ${total_paid:.2f}\n"
        message += f"**Pending Payments:** ${total_pending:.2f}\n"
        message += f"**Amount Due:** ${balance_due:.2f}\n\n"
        
        if item_sales:
            message += "📊 **TOP SELLING ITEMS** 📊\n"
            sorted_items = sorted(item_sales.items(), key=lambda x: x[1], reverse=True)
            for item, quantity in sorted_items[:10]:
                message += f"• **{item}:** {quantity} sold\n"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error getting sales summary: {e}")
        update.message.reply_text("Error retrieving sales summary.")

def balance_command(update: Update, context: CallbackContext):
    """Shows balance for user or specified client (CM only)."""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    target_user_id = user_id
    target_user_name = user_name
    
    # If CM wants to check someone else's balance
    if is_cm(user_id) and context.args:
        try:
            target_user_id = int(context.args[0])
            # Get user name from their data
            client_doc = get_client_ref(target_user_id).get()
            if client_doc.exists:
                target_user_name = client_doc.to_dict().get('user_name', f'User {target_user_id}')
            else:
                target_user_name = f'User {target_user_id}'
        except ValueError:
            update.message.reply_text("Usage: /balance <user_id>\nExample: /balance 12345")
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own balance. Use /balance without arguments.")
        return
    
    try:
        client_ref = get_client_ref(target_user_id)
        
        # Calculate total orders
        orders = client_ref.collection('orders').stream()
        total_ordered = 0
        for order in orders:
            try:
                order_data = order.to_dict()
                if order_data:
                    total_ordered += order_data.get('total_price', 0)
            except Exception as e:
                logger.warning(f"Error processing order for balance calculation: {e}")
                continue
        
        # Calculate total payments
        payments = client_ref.collection('payments').stream()
        total_paid = 0
        for payment in payments:
            try:
                payment_data = payment.to_dict()
                if payment_data:
                    total_paid += payment_data.get('amount', 0)
            except Exception as e:
                logger.warning(f"Error processing payment for balance calculation: {e}")
                continue
        
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
        
        message = f"{status_emoji} **BALANCE - {target_user_name}** {status_emoji}\n\n"
        message += f"Total Ordered: ${total_ordered:.2f}\n"
        message += f"Total Paid: ${total_paid:.2f}\n"
        message += f"**{status_text}: ${abs(balance):.2f}**"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error calculating balance for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve balance for {target_user_name}.")

def summary_command(update: Update, context: CallbackContext):
    """Shows order and payment summary."""
    user_id = update.message.from_user.id
    user_name = get_user_display_name(update.message.from_user)
    
    target_user_id = user_id
    target_user_name = user_name
    
    # If CM wants to check someone else's summary
    if is_cm(user_id) and context.args:
        try:
            target_user_id = int(context.args[0])
            client_doc = get_client_ref(target_user_id).get()
            if client_doc.exists:
                target_user_name = client_doc.to_dict().get('user_name', f'User {target_user_id}')
            else:
                target_user_name = f'User {target_user_id}'
        except ValueError:
            update.message.reply_text("Usage: /summary <user_id>\nExample: /summary 12345")
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own summary. Use /summary without arguments.")
        return
    
    try:
        client_ref = get_client_ref(target_user_id)
        
        message = f"📊 **SUMMARY - {target_user_name}** 📊\n\n"
        
        # Recent Orders - avoid order_by to prevent index issues
        message += "🍽️ **RECENT ORDERS**\n"
        try:
            orders = list(client_ref.collection('orders').limit(10).stream())
            
            # Sort in Python by timestamp
            orders_list = []
            for doc in orders:
                try:
                    order_data = doc.to_dict()
                    if order_data:
                        orders_list.append(order_data)
                except Exception as e:
                    logger.warning(f"Error processing order in summary: {e}")
                    continue
            
            orders_list.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)
            
            total_ordered = 0
            if orders_list:
                for order in orders_list:
                    timestamp = order.get('timestamp')
                    time_str = timestamp.strftime('%m-%d') if timestamp else 'N/A'
                    quantity = order.get('quantity', 0)
                    item_name = order.get('item_name', 'Unknown')
                    order_total = order.get('total_price', 0)
                    message += f"• {quantity}x {item_name} - ${order_total:.2f} _{time_str}_\n"
                    total_ordered += order_total
            else:
                message += "No orders found.\n"
        except Exception as e:
            logger.warning(f"Error getting orders for summary: {e}")
            message += "Error loading orders.\n"
            total_ordered = 0
        
        message += f"\n**Total Ordered: ${total_ordered:.2f}**\n\n"
        
        # Recent Payments - avoid order_by to prevent index issues
        message += "💰 **RECENT PAYMENTS**\n"
        try:
            payments = list(client_ref.collection('payments').limit(10).stream())
            
            # Sort in Python by timestamp
            payments_list = []
            for doc in payments:
                try:
                    payment_data = doc.to_dict()
                    if payment_data:
                        payments_list.append(payment_data)
                except Exception as e:
                    logger.warning(f"Error processing payment in summary: {e}")
                    continue
            
            payments_list.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)
            
            total_paid = 0
            if payments_list:
                for payment in payments_list:
                    timestamp = payment.get('timestamp')
                    time_str = timestamp.strftime('%m-%d') if timestamp else 'N/A'
                    amount = payment.get('amount', 0)
                    message += f"• ${amount:.2f} _{time_str}_\n"
                    total_paid += amount
            else:
                message += "No payments found.\n"
        except Exception as e:
            logger.warning(f"Error getting payments for summary: {e}")
            message += "Error loading payments.\n"
            total_paid = 0
        
        message += f"\n**Total Paid: ${total_paid:.2f}**\n\n"
        
        # Balance
        balance = total_ordered - total_paid
        if balance > 0:
            message += f"💳 **Amount Due: ${balance:.2f}**"
        elif balance < 0:
            message += f"💰 **Credit: ${abs(balance):.2f}**"
        else:
            message += "✅ **All Paid Up!**"
        
        # Split long messages
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error generating summary for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve summary for {target_user_name}.")

def help_command(update: Update, context: CallbackContext):
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
    
    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

def error_handler(update: object, context: CallbackContext):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    if update and hasattr(update, 'message') and update.message:
        try:
            update.message.reply_text("An unexpected error occurred. Please try again later.")
        except Exception:
            logger.error("Could not send error message to user")

def main():
    """Start the bot."""
    if not TOKEN or TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logger.error("Bot token is not set properly. Please check TELEGRAM_BOT_TOKEN.")
        return
    
    if CM_USER_ID == 0:
        logger.error("CM User ID is not set properly. Please check CM_USER_ID.")
        return
    
    try:
        # Create the Updater and pass it your bot's token
         application = Application.builder().token(TOKEN).build()
         ##   updater = Updater(TOKEN)
         ##  dispatcher = updater.dispatcher
        
        # Register handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", menu_command))
        application.add_handler(CommandHandler("order", order_command))
        application.add_handler(CommandHandler("paid", paid_command))
        application.add_handler(CommandHandler("received", received_command))
        application.add_handler(CommandHandler("pending", pending_command))
        application.add_handler(CommandHandler("clients", clients_command))
        application.add_handler(CommandHandler("orders", orders_command))
        application.add_handler(CommandHandler("sales", sales_command))
        application.add_handler(CommandHandler("balance", balance_command))
        application.add_handler(CommandHandler("summary", summary_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # Add error handler
        dispatcher.add_error_handler(error_handler)
        
        logger.info("Bot setup completed successfully")
        
    except Exception as e:
        logger.error(f"Error setting up bot: {e}")
        raise

# ---------- Flask App ----------
#----------------------------------------------------------------
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start_command))

@app.route("/", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "", 200

if __name__ == "__main__":
    railway_url = os.getenv('RAILWAY_URL')
    if railway_url:
     try:
        asyncio.run(application.bot.set_webhook(f"{railway_url}/"))
#------------------------------------------------------------------------------
        logger.info(f"Webhook set to: {railway_url}/")
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
   else:
        logger.warning("RAILWAY_URL not set - webhook not configured")
    
   app.run(host="0.0.0.0", port=PORT)