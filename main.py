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

# Initialize Firebase and bot at module level
initialize_firebase()

# Global variable to store the updater
updater = None

def setup_bot():
    """Set up the Telegram bot with webhook."""
    global updater
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    for handler in get_handlers():
        dispatcher.add_handler(handler)
    
    dispatcher.add_error_handler(error_handler)
    
    return updater

# Initialize bot immediately
if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != 'YOUR_TELEGRAM_BOT_TOKEN':
    setup_bot()
    logger.info("Bot setup completed at module level")

# Define webhook route OUTSIDE of main function
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram."""
    try:
        if updater is None:
            logger.error("Updater not initialized")
            return 'Bot not initialized', 500
            
        update = Update.de_json(request.get_json(force=True), updater.bot)
        updater.dispatcher.process_update(update)
        logger.info("Webhook processed successfully")
        return '', 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return '', 500

# Add a health check route
@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint."""
    if updater is None:
        return "Bot not initialized", 500
    return "Cafeteria Bot is running! 🤖", 200


def main():
    """Main function to start the bot with webhook for Railway."""
    global updater
    
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logger.error("Bot token is not set properly. Please check TELEGRAM_BOT_TOKEN.")
        return
    
    if CM_USER_ID == 0:
        logger.error("CM User ID is not set properly. Please check CM_USER_ID.")
        return
    
    # Setup bot if not already done
    if updater is None:
        setup_bot()
    
    # Get the PORT environment variable set by Railway
    port = int(os.environ.get('PORT', 8080))
    
    # Construct webhook URL (Railway provides a public URL)
    webhook_url = f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN')}/webhook"
    
    # Set webhook
    updater.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")
    
    # Start Flask server
    logger.info("Starting Cafeteria Bot on Railway...")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()