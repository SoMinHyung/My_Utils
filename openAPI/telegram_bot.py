import json
import telegram

class Telegram_bot():
    def __init__(self):
        token = '1823385800:AAHxmzCrlE6uSQcb41Ie4BqwD0ZiK5wIeBo'
        self.bot = telegram.Bot(token=token)

    def sendMessage(self, msg):
        msg_json = json.dumps(msg, ensure_ascii=False)
        self.bot.sendMessage(chat_id='@noticechannelupbbbit', text=msg_json)

    def getmsglog(self):
        updates = self.bot.getUpdates()
        for u in updates:
            print(u.message)

if __name__ == "__main__":
    bot = Telegram_bot()
    # bot.getmsglog()
    bot.sendMessage("hello")