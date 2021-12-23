import json
import telegram
import configparser
#telethon
import telethon.sync
from telethon import TelegramClient

class Telegram_Crawler():
    def __init__(self):
        # Reading Configs
        config = configparser.ConfigParser()
        config.read("config.ini")

        # Setting configuration values & Log in
        api_id = config['Telegram']['api_id']
        api_hash = config['Telegram']['api_hash']
        phone = config['Telegram']['phone']
        username = config['Telegram']['username']
        self.client = self._log_in(api_id, api_hash, phone, username)

    def _log_in(self,api_id, api_hash, phone, username):
        client = TelegramClient(username, api_id, api_hash)
        client.start(phone=phone)
        print("Client Created")
        return client

    def get_channel_msg(self, count=1):
        target_channel = 'shrimp_notice'
        msgs = self.client.get_messages(target_channel, count)
        return msgs

if __name__ == "__main__":
    crawler = Telegram_Crawler()
    crawler.get_channel_msg()