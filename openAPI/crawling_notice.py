import re
import requests
import random
import time
import json

class Crawling_bot():
    def __init__(self):
        self.newest_news = {}
        self.upbit_url = 'https://api-manager.upbit.com/api/v1/notices?page=1&per_page=20&thread_name=general'
        self.upbit_listing_url = 'https://api-manager.upbit.com/api/v1/notices/search?search=%5B%EA%B1%B0%EB%9E%98%5D&page=1&per_page=20&before=&target=non_ios&thread_name=general'
        self.upbit_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:66.0) Gecko/20100101 Firefox/66.0",
            "Accept-Encoding": "*",
            "Connection": "keep-alive"
        }
        self.upbit_newest_news = None
        self.bithumb_url = ''
        self.bithumb_headers = {}
        self.bithumb_newest_news = None

    def crawl_upbit(self):
        req = requests.get(self.upbit_url, headers=self.upbit_headers)
        crawled_data = req.json()
        new = crawled_data['data']['list'][0]['title']
        if self.upbit_newest_news is None:
            self.upbit_newest_news = new
            print(self.upbit_newest_news)
        return new

    def crawl_bithumb(self):
        req = requests.get()







    def crawl_bithumb(self):
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://cafe.bithumb.com",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.92 Safari/537.36",
            "DNT": "1",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://cafe.bithumb.com/view/boards/43",
            "Accept-Encoding": "gzip, deflate, br"
        }

        string = """columns[0][data]=0&columns[0][name]=&columns[0][searchable]=true&columns[0][orderable]=false&columns[0][search][value]=&columns[0][search][regex]=false&columns[1][data]=1&columns[1][name]=&columns[1][searchable]=true&columns[1][orderable]=false&columns[1][search][value]=&columns[1][search][regex]=false&columns[2][data]=2&columns[2][name]=&columns[2][searchable]=true&columns[2][orderable]=false&columns[2][search][value]=&columns[2][search][regex]=false&columns[3][data]=3&columns[3][name]=&columns[3][searchable]=true&columns[3][orderable]=false&columns[3][search][value]=&columns[3][search][regex]=false&columns[4][data]=4&columns[4][name]=&columns[4][searchable]=true&columns[4][orderable]=false&columns[4][search][value]=&columns[4][search][regex]=false&start=30&length=30&search[value]=&search[regex]=false"""

        article_root = "https://cafe.bithumb.com/view/board-contents/{}"

        for page in range(1, 4):
            with requests.Session() as s:
                s.headers.update(headers)

                data = {"draw": page}
                data.update({ele[:ele.find("=")]: ele[ele.find("=") + 1:] for ele in string.split("&")})
                data["start"] = 30 * (page - 1)

                r = s.post('https://cafe.bithumb.com/boards/43/contents', data=data,
                           verify=False)  # set verify = False while you are using fiddler

                json_data = json.loads(r.text).get("data")  # transform string to dict then we can extract data easier
                for each in json_data:
                    url = article_root.format(each[0])
                    print(url)




    def start_monitoring(self, rate):
        count = 0
        Monitoring = True
        while Monitoring:
            count +=1

            req = requests.get(self.url, headers=self.headers)
            crawled_data = req.json()
            time.sleep(random.uniform(1,3))

            new = crawled_data['data']['list'][0]
            if count == 1:
                newest_news = new
                self.bot.sendMessage(msg=newest_news)
                newest_news = newest_news
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

if __name__=="__main__":
    bot = Crawling_bot()

    bot.crawl_upbit()