import csv
import json
import re
import requests
import random
import time
from telegram_bot import Telegram_bot
from binance import Binance
from upbit import Upbit

class Crawling_upbit_notice():
    def __init__(self):
        self.bot = Telegram_bot()

        with open('key.txt', 'r') as f:
            keys = list(csv.reader(f, delimiter="/"))
        self.upbit = Upbit(upbit_access_key=keys[0][1], upbit_secret_key=keys[0][2])
        self.binance = Binance(binance_access_key=keys[1][1], binance_secret_key=keys[1][2])

        self.newest_news = {}
        self.url = 'https://api-manager.upbit.com/api/v1/notices?page=1&per_page=20&thread_name=general'
        self.listing_url = 'https://api-manager.upbit.com/api/v1/notices/search?search=%5B%EA%B1%B0%EB%9E%98%5D&page=1&per_page=20&before=&target=non_ios&thread_name=general'
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:66.0) Gecko/20100101 Firefox/66.0",
            "Accept-Encoding": "*",
            "Connection": "keep-alive"
        }

    def start_monitoring(self, rate):
        count = 0
        Monitoring = True
        while Monitoring:
            count +=1

            req = requests.get(self.url, headers=self.headers)
            crawled_data = req.json()
            time.sleep(random.uniform(2,4))

            new = crawled_data['data']['list'][1]
            if count == 1:
                newest_news = new
            new_title = new['title']

            print(count)
            print(new_title)

            if newest_news['title'] != new_title:
                if '[거래]' in new_title:
                    if 'BTC' in new_title:
                        ticker = re.findall('[A-Z]+', new_title)
                        ticker.remove('BTC')
                        order_rate = rate / len(ticker)
                        # for tick in ticker:
                        #     self.binance.create_market_order(tick,'sell', order_rate) # buy , sell
                        print('BTC 상장', ticker)
                        Monitoring = False
                    elif '원화' in new_title:
                        ticker = re.findall('[A-Z]+', new_title)
                        if self.upbit.KRW > 5000:
                            self.upbit.create_market_order('KRW-BTC', 'buy', rate)
                        order_rate = 1 / len(ticker)
                        for tick in ticker:
                            currency = 'BTC-' + tick
                            self.upbit.create_market_order(currency, 'buy', order_rate)
                        print("원화 추가", ticker)
                        Monitoring = False

                self.bot.sendMessage(msg=new)
                newest_news = new

if __name__ == "__main__":
    crawler = Crawling_upbit_notice()
    crawler.start_monitoring(1)