import telegram

class Telegram_bot():
    def __init__(self):
        token = '***REMOVED_TELEGRAM_BOT_TOKEN***'
        self.bot = telegram.Bot(token=token)

    def sendMessage(self, msg):
        self.bot.sendMessage(chat_id='***REMOVED_CHAT_ID***', text=msg)