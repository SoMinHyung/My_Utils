import ccxt



class Binance():
    def __init__(self, binance_access_key, binance_secret_key):
        self.binance = ccxt.binance({
            'apiKey' : binance_access_key,
            'secret' : binance_secret_key,
            'options': { 'adjustForTimeDifference': True}
        })

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
    binance = Binance()
    order = binance.create_limit_order('XRP/BTC', 'buy', 10, 0.000025)
    # order = binance.create_market_order('XRP/BTC', 'buy', 1)
    # binance.create_market_order('XRP/BTC','buy',0.005)