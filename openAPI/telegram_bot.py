import telegram

class Telegram_bot():
    def __init__(self):
        token = '1823385800:AAHxmzCrlE6uSQcb41Ie4BqwD0ZiK5wIeBo'
        self.bot = telegram.Bot(token=token)

    def sendMessage(self, msg):
        self.bot.sendMessage(chat_id='1148111890', text=msg)