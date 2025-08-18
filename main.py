import os
import logging
from telegram import Update
from telegram.ext import Updater, Dispatcher
from coreCMfunc04 import initialize_firebase, get_handlers, error_handler, TELEGRAM_BOT_TOKEN, CM_USER_ID
from flask import Flask, request

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def setup_bot():
    """Set up the Telegram bot with webhook."""
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    for handler in get_handlers():
        dispatcher.add_handler(handler)
    
    dispatcher.add_error_handler(error_handler)
    
    return updater

def main():
    """Main function to start the bot with webhook for Railway."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logger.error("Bot token is not set properly. Please check TELEGRAM_BOT_TOKEN.")
        return
    
    if CM_USER_ID == 0:
        logger.error("CM User ID is not set properly. Please check CM_USER_ID.")
        return
    
    initialize_firebase()
    
    updater = setup_bot()
    
    # Get the PORT environment variable set by Railway
    port = int(os.environ.get('PORT', 8443))
    
    # Construct webhook URL (Railway provides a public URL)
    webhook_url = f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN')}/webhook"
    
    # Set webhook
    updater.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")
    
    @app.route('/webhook', methods=['POST'])
    def webhook():
        update = Update.de_json(request.get_json(force=True), updater.bot)
        updater.dispatcher.process_update(update)
        return '', 200
    
    # Start Flask server
    app.run(host='0.0.0.0', port=port)
    logger.info("Personal Cafeteria Bot started on Railway...")

if __name__ == '__main__':
    main()