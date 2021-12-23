import time, random
import re
import json
import configparser

import telegram

from telegram_bot import Telegram_Crawler

from crawling_notice import Crawling_bot

# def run(crawl_bot, buy_bot, upbit=False, bithumb=False):
#
#     while True:
#         # if bithumb:
#         #     print("bithumb")
#         #     bithumb_title = crawl_bot.crawl_bithumb()
#         #     if crawl_bot.bithumb_newest_news != bithumb_title:
#         #         print(bithumb_title)
#         #         crawl_bot.bithumb_newest_news = bithumb_title
#         #         if ....
#
#
#         time.sleep(random.uniform(0, 1)

class mainclass():
    def __init__(self):
        self.latest_news = None


        # Setting configuration values & Log in
        config = configparser.ConfigParser()
        config.read("config.ini")

        # Set Channel Bot
        token = config['Telegram']['bot']
        self.bot = telegram.Bot(token=token)

        # Start Crawler
        self.crawler = Telegram_Crawler()

    def check_upbit(self, msg):
        if msg.startswith('업비트') :
            # [안내] 니어프로토콜(NEAR) 입출금 일시 중단 안내
            if '[거래]' in msg:
                print(msg)
                ticker = re.findall('[A-Z]\w+', msg)
                if 'BTC' in ticker:
                    ticker.remove('BTC')
                if 'KRW' in ticker:
                    ticker.remove('KRW')
                # ticker = ['NEAR', 'YGG']
                buy_bot.buy(ticker)

    def sendMessage(self, msg):
        msg_json = json.dumps(msg, ensure_ascii=False)
        self.bot.sendMessage(chat_id='@noticechannelupbbbit', text=msg_json)

    def check(self):
        msgs = self.crawler.get_channel_msg(count=100)

        for msg in msgs:
            if self.latest_news is None:
                self.latest_news = msg.message
                # self.sendMessage(self.latest_news)

            if self.latest_news != msg.message:
                self.latest_news = msg.message

                self.check_upbit(self.latest_news)

                # self.sendMessage(latest_news)


        print(msg)

if __name__=="__main__":

    main_module = mainclass()
    main_module.check()

    """
    빗썸(Bithumb) 공지변경 - [이벤트] 고객확인제도 감사 이벤트 – 모든 고객에게 코인 “1개”를 드립니다!
    2021-12-20 16:48:39 🔔
    """
    out = "[거래] KRW, BTC 마켓 디지털 자산 추가 (NEAR, YGG)"
    ticker = re.findall('[A-Z]\w+', out)
    if 'BTC' in ticker:
        ticker.remove('BTC')
    if 'KRW' in ticker:
        ticker.remove('KRW')

    print(ticker)
    print("Start Crawling Listing info")
    # run(crawl_bot = crawl_bot, upbit=True, bithumb=False)