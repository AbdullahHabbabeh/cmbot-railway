import logging
import os
from telegram import Update, ParseMode
from telegram.ext import CommandHandler, CallbackContext
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
# Bot Configuration - Updated for permanent use
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8378603838:AAHfhlrpZ8G6eNXk2l4HSxWbf85H4cIWp1k')
CM_USER_ID = int(os.environ.get('CM_USER_ID', '135976546'))  # CM User ID

# Menu Configuration - Cafeteria Man can modify this
MENU = {
    'coffee': {'name': 'Coffee', 'price': 2.50},
    'tea': {'name': 'Tea', 'price': 2.00},
    'sandwich': {'name': 'Sandwich', 'price': 5.00},
    'burger': {'name': 'Burger', 'price': 8.00},
    'pizza': {'name': 'Pizza Slice', 'price': 4.50},
    'salad': {'name': 'Salad', 'price': 6.00},
    'juice': {'name': 'Fresh Juice', 'price': 3.50},
    'cake': {'name': 'Cake Slice', 'price': 4.00},
}

# Firestore Setup
def initialize_firebase():
  import json
import io

def initialize_firebase():
    """Initialize Firebase from the JSON stored in GOOGLE_APPLICATION_CREDENTIALS_JSON."""
    try:
        if firebase_admin._apps:
            logging.info("Firebase already initialized, skipping.")
            return

        # 1) Try Railway / Canvas env-var first
        cred_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if cred_json:
            service_info = json.loads(cred_json)
            cred = credentials.Certificate(service_info)
            project_id = service_info.get("project_id", "default-app-id")
            firebase_admin.initialize_app(cred, {"projectId": project_id})
            logging.info("Firebase initialized from GOOGLE_APPLICATION_CREDENTIALS_JSON")
            return

        # 2) Fallback to ApplicationDefault (Cloud Run / Cloud Functions)
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("K_SERVICE"):
            cred = credentials.ApplicationDefault()
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "default-app-id")
            firebase_admin.initialize_app(cred, {"projectId": project_id})
            logging.info("Firebase initialized with ApplicationDefault")
            return

        # 3) Final fallback to local serviceAccountKey.json
        cred_path = "serviceAccountKey.json"
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logging.info("Firebase initialized with local serviceAccountKey.json")
        else:
            raise FileNotFoundError(
                "Firebase credentials not found in env-var nor in serviceAccountKey.json"
            )

    except Exception as e:
        logging.error("Error initializing Firebase: %s", e)
        raise

# Initialize Firebase once at module level
initialize_firebase()
db = firestore.client()

# --- Helper Functions ---
def get_pending_payments_ref():
    """Returns a Firestore reference to pending payments collection."""
    return db.collection('pending_payments')

def get_user_display_name(user):
    """Gets a display name for the user (username or first_name)."""
    if user.username:
        return f"@{user.username}"
    return user.first_name

def get_client_ref(user_id):
    """Returns a Firestore reference to a specific client's data."""
    return db.collection('cafeteria_clients').document(str(user_id))

def is_cm(user_id) -> bool:
    """Checks if the user is the Cafeteria Man (bot owner)."""
    return user_id == CM_USER_ID

def notify_cm(context: CallbackContext, message: str, parse_mode=ParseMode.MARKDOWN):
    """Helper function to notify CM with error handling."""
    if not CM_USER_ID:
        logger.warning("CM_USER_ID not set, cannot send notification")
        return False
    
    try:
        context.bot.send_message(
            chat_id=CM_USER_ID,
            text=message,
            parse_mode=parse_mode
        )
        logger.info(f"CM notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to notify CM: {e}")
        return False

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    user_id = user.id
    
    if is_cm(user_id):
        welcome_msg = (
            f"Welcome back, Cafeteria Manager! 👨‍🍳\n\n"
            f"You can use:\n"
            f"/menu - View/manage menu items\n"
            f"/received - Confirm payment received\n"
            f"/balance <user_id> - Check any client's balance\n"
            f"/summary <user_id> - View client's order history\n"
            f"/pending - View pending payments\n"
            f"/help - Show all commands"
        )
    else:
        welcome_msg = (
            f"Hi {user.first_name}! 🍽️\n\n"
            f"Welcome to the Cafeteria Bot. You can:\n"
            f"/menu - View available items\n"
            f"/order - Place your food order\n"
            f"/paid - Report when you've made a payment\n"
            f"/balance - Check your current balance\n"
            f"/summary - View your order history\n"
            f"/help - Show all commands"
        )
    
    update.message.reply_text(welcome_msg)

def menu_command(update: Update, context: CallbackContext) -> None:
    """Shows the menu with available items and prices."""
    user_id = update.message.from_user.id
    
    menu_text = "🍽️ **CAFETERIA MENU** 🍽️\n\n"
    
    for key, item in MENU.items():
        menu_text += f"**{item['name']}** - ${item['price']:.2f}\n"
        menu_text += f"  _Order with: /order {key} <quantity>_\n\n"
    
    menu_text += "💡 **How to order:** `/order <item_code> <quantity>`\n"
    menu_text += "📝 **Example:** `/order coffee 2` (for 2 coffees)"
    
    update.message.reply_markdown(menu_text)

def order_command(update: Update, context: CallbackContext) -> None:
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
        
        client_ref.set({
            'user_name': user_name,
            'user_id': user_id,
            'last_order': firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        update.message.reply_text(
            f"✅ Order placed successfully!\n\n"
            f"**{quantity} x {item['name']}** @ ${item['price']:.2f} each\n"
            f"**Total: ${total_price:.2f}**\n\n"
            f"Your order has been sent to the cafeteria. 🍽️"
        )
        
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

def paid_command(update: Update, context: CallbackContext) -> None:
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
        
        pending_data = {
            'user_id': user_id,
            'user_name': user_name,
            'amount': amount,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'status': 'pending_confirmation'
        }
        
        pending_ref = get_pending_payments_ref().add(pending_data)
        
        update.message.reply_text(
            f"💰 Payment reported: ${amount:.2f}\n\n"
            f"Your payment is pending confirmation from the cafeteria manager. "
            f"You'll be notified once it's confirmed. ⏳"
        )
        
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

def orders_command(update: Update, context: CallbackContext) -> None:
    """CM views recent orders from all clients."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all orders.")
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
                except Exception as e:
                    logger.warning(f"Error getting orders for client {client_name}: {e}")
                    continue
            except Exception as e:
                logger.warning(f"Error processing client data: {e}")
                continue
        
        if not all_orders:
            update.message.reply_text("No orders found.")
            return
        
        all_orders.sort(key=lambda x: x.get('timestamp', datetime.min), reverse=True)
        
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
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        update.message.reply_text("Error retrieving orders. Please try again later.")

def test_notification_command(update: Update, context: CallbackContext) -> None:
    """Test command for CM to verify notifications work."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can test notifications.")
        return
    
    try:
        context.bot.send_message(
            chat_id=CM_USER_ID,
            text="✅ **NOTIFICATION TEST**\n\nIf you see this message, notifications are working correctly!",
            parse_mode=ParseMode.MARKDOWN
        )
        update.message.reply_text("Test notification sent! Check if you received it.")
    except Exception as e:
        logger.error(f"Test notification failed: {e}")
        update.message.reply_text(f"❌ Notification test failed: {e}")

def pending_command(update: Update, context: CallbackContext) -> None:
    """CM views all pending payments."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view pending payments.")
        return
    
    try:
        pending_payments = list(get_pending_payments_ref()
                              .where('status', '==', 'pending_confirmation')
                              .stream())
        
        if not pending_payments:
            update.message.reply_text("✅ No pending payments!")
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
            
            message += f"{i}. **{user_name}** - ${amount:.2f} _{time_str}_\n"
            total_pending += amount
        
        message += f"\n**Total Pending: ${total_pending:.2f}**\n\n"
        message += f"Use `/received <number>` to confirm payments"
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error getting pending payments: {e}")
        update.message.reply_text("Error retrieving pending payments. Please try again later.")

def clients_command(update: Update, context: CallbackContext) -> None:
    """CM views all clients and their balances."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can view all clients.")
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
                
                actual_client_id = user_id_from_data or client_id
                
                try:
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
                    except Exception as e:
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
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error getting clients list: {e}")
        update.message.reply_text("Error retrieving clients list. Please try again later.")

def received_command(update: Update, context: CallbackContext) -> None:
    """CM confirms receipt of payment."""
    user_id = update.message.from_user.id
    
    if not is_cm(user_id):
        update.message.reply_text("Only the cafeteria manager can confirm payments.")
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
            update.message.reply_text("No pending payments to confirm.")
            return
        
        if not context.args:
            message = "💰 **PENDING PAYMENTS** 💰\n\n"
            for i, (doc, payment) in enumerate(pending_list, 1):
                message += f"{i}. {payment['user_name']} - ${payment['amount']:.2f}\n"
            
            message += f"\nUse `/received <number>` to confirm a payment\n"
            message += f"Example: `/received 1` to confirm the first payment"
            
            update.message.reply_markdown(message)
            return
        
        payment_index = int(context.args[0]) - 1
        
        if payment_index < 0 or payment_index >= len(pending_list):
            update.message.reply_text(f"Invalid payment number. Use /received to see pending payments.")
            return
        
        payment_doc, payment_data = pending_list[payment_index]
        
        client_ref = get_client_ref(payment_data['user_id'])
        client_ref.collection('payments').add({
            'amount': payment_data['amount'],
            'user_id': payment_data['user_id'],
            'user_name': payment_data['user_name'],
            'confirmed_by_cm': True,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'original_timestamp': payment_data['timestamp']
        })
        
        payment_doc.reference.delete()
        
        update.message.reply_text(
            f"✅ Payment confirmed!\n\n"
            f"**${payment_data['amount']:.2f}** from **{payment_data['user_name']}**"
        )
        
        try:
            context.bot.send_message(
                chat_id=payment_data['user_id'],
                text=f"✅ Your payment of ${payment_data['amount']:.2f} has been confirmed! 🎉",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to notify client about confirmed payment: {e}")
        
        logger.info(f"CM confirmed payment: ${payment_data['amount']:.2f} from {payment_data['user_name']}")
        
    except (ValueError, IndexError):
        update.message.reply_text("Usage: /received <payment_number>\nUse /received to see pending payments.")
    except Exception as e:
        logger.error(f"Error processing received command: {e}")
        update.message.reply_text("Sorry, there was an error confirming the payment. Please try again.")

def sales_command(update: Update, context: CallbackContext) -> None:
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
            client_data = client_doc.to_dict()
            client_id = client_data.get('user_id')
            client_ref = get_client_ref(client_id)
            
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
            
            payments = list(client_ref.collection('payments').stream())
            for payment_doc in payments:
                payment = payment_doc.to_dict()
                total_paid += payment.get('amount', 0)
        
        pending_payments = list(get_pending_payments_ref()
                              .where('status', '==', 'pending_confirmation')
                              .stream())
        for pending_doc in pending_payments:
            pending = pending_doc.to_dict()
            total_pending += pending.get('amount', 0)
        
        balance_due = total_ordered - total_paid
        
        message += f"**Total Orders:** ${total_ordered:.2f}\n"
        message += f"**Total Paid:** ${total_paid:.2f}\n"
        message += f"**Pending Payments:** ${total_pending:.2f}\n"
        message += f"**Amount Due:** ${balance_due:.2f}\n\n"
        
        message += "📊 **TOP SELLING ITEMS** 📊\n"
        sorted_items = sorted(item_sales.items(), key=lambda x: x[1], reverse=True)
        for item, quantity in sorted_items[:10]:
            message += f"• **{item}:** {quantity} sold\n"
        
        if len(message) > 4000:
            message = message[:4000] + "\n... (truncated)"
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error getting sales summary: {e}")
        update.message.reply_text("Error retrieving sales summary.")

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
            update.message.reply_text("Usage: /balance <user_id>\nExample: /balance 12345")
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own balance. Use /balance without arguments.")
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
        
        message = f"{status_emoji} **BALANCE - {target_user_name}** {status_emoji}\n\n"
        message += f"Total Ordered: ${total_ordered:.2f}\n"
        message += f"Total Paid: ${total_paid:.2f}\n"
        message += f"**{status_text}: ${abs(balance):.2f}**"
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error calculating balance for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve balance for {target_user_name}.")

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
            update.message.reply_text("Usage: /summary <user_id>\nExample: /summary 12345")
            return
    elif not is_cm(user_id) and context.args:
        update.message.reply_text("You can only check your own summary. Use /summary without arguments.")
        return
    
    try:
        client_ref = get_client_ref(target_user_id)
        
        message = f"📊 **SUMMARY - {target_user_name}** 📊\n\n"
        
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
                message += f"• {order.get('quantity')}x {order.get('item_name')} - ${order.get('total_price', 0):.2f} _{time_str}_\n"
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
        
        update.message.reply_markdown(message)
        
    except Exception as e:
        logger.error(f"Error generating summary for {target_user_name}: {e}")
        update.message.reply_text(f"Could not retrieve summary for {target_user_name}.")

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
    
    update.message.reply_markdown(help_text)

def error_handler(update: object, context: CallbackContext) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    if update and hasattr(update, 'message') and update.message:
        update.message.reply_text("An unexpected error occurred. Please try again later.")

def get_handlers():
    """Returns a list of command handlers."""
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