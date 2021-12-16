import time, random
import re

from crawling_notice import Crawling_bot

def run(crawl_bot, buy_bot, upbit=False, bithumb=False):

    while True:
        if bithumb:
            print("bithumb")
            bithumb_title = crawl_bot.crawl_bithumb()
            if crawl_bot.bithumb_newest_news != bithumb_title:
                print(bithumb_title)
                crawl_bot.bithumb_newest_news = bithumb_title
                if ....
        if upbit:
            print("upbit")
            #[안내] 니어프로토콜(NEAR) 입출금 일시 중단 안내
            upbit_title = crawl_bot.crawl_upbit()
            if crawl_bot.upbit_newest_news != upbit_title:
                print(upbit_title)
                crawl_bot.upbit_newest_news = upbit_title
                if '[거래]' in upbit_title:
                    print(upbit_title)
                    ticker = re.findall('[A-Z]\w+', upbit_title)
                    if 'BTC' in ticker:
                        ticker.remove('BTC')
                    if 'KRW' in ticker:
                        ticker.remove('KRW')
                    # ticker = ['NEAR', 'YGG']
                    buy_bot.buy(ticker)

        time.sleep(random.uniform(0, 1)


if __name__=="__main__":
    out = "[거래] KRW, BTC 마켓 디지털 자산 추가 (NEAR, YGG)"
    ticker = re.findall('[A-Z]\w+', out)
    if 'BTC' in ticker:
        ticker.remove('BTC')
    if 'KRW' in ticker:
        ticker.remove('KRW')

    print(ticker)
    print("Start Crawling Listing info")
    # crawl_bot = Crawling_bot()
    # run(crawl_bot = crawl_bot, upbit=True, bithumb=False)