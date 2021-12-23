import ccxt
import csv


class Binance():
    def __init__(self, binance_access_key, binance_secret_key):
        self.binance = ccxt.binance({
            'apiKey' : binance_access_key,
            'secret' : binance_secret_key,
            'options': { 'adjustForTimeDifference': True},
            'verbose': True
        })

        self.only_btc_tickers, self.only_usdt_tickers, self.both_tickers = self._get_tickers()

    def _get_tickers(self):
        tickers = self.binance.fetch_tickers()
        keys = tickers.keys()
        btc_count = []
        usdt_count = []
        only_usdt = []
        for key in keys:
            if '/BTC' in key:
                key = key.replace("/BTC","")
                btc_count.append(key)
            if '/USDT' in key:
                key = key.replace("/USDT","")
                usdt_count.append(key)

        for usdt in usdt_count:
            if usdt in btc_count:
                btc_count.remove(usdt)
            else:
                only_usdt.append(usdt)
        return btc_count, only_usdt, usdt_count

    def get_wallet_status(self,ticker=None):
        out = self.binance.fetch_status()
        print(out)

    def create_limit_order(self, ticker, side, amount, price):
        order = self.binance.create_limit_order(symbol=ticker, side=side, amount=amount, price=price)
        return order

    def create_market_order(self,ticker, side, proportion):
        amount = self._get_max_position_available(ticker, proportion)
        order = self.binance.create_market_order(symbol=ticker, side=side, amount=amount)
        if order['status'] == 'open':
            print('Market order is canceled and I\'ll Try again automatically')
            self.binance.cancel_all_orders(ticker)
            self.create_market_order(symbol=ticker, side=side, amount=amount)
        print(amount)

    def _get_max_position_available(self, ticker, proportion):
        base_money = ticker.split("/")[1]
        to_use = float(self.binance.fetch_balance().get(base_money).get('free'))
        price = float(self.binance.fetchTicker(ticker).get('last'))
        decide_position_to_use = int((to_use / price) * proportion)
        return decide_position_to_use

    # def withdraw_order(self):
    #
    #
    # def _check_wallet(self):

if __name__ == '__main__':

    with open('key.txt', 'r') as f:
        keys = list(csv.reader(f, delimiter="/"))
    binance = Binance(binance_access_key=keys[1][1], binance_secret_key=keys[1][2])
    # binance.get_wallet_status()
    print(binance.only_btc_tickers)
    # order = binance.create_limit_order('XRP/BTC', 'buy', 10, 0.000025)
    # order = binance.create_market_order('XRP/BTC', 'buy', 1)
    # binance.create_market_order('XRP/BTC','buy',0.005)