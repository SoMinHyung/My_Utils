import threading
import os
import re
import time
import math
import jwt
import uuid
import hashlib
from urllib.parse import urlencode
import requests

class Upbit():
    def __init__(self, upbit_access_key, upbit_secret_key):
        self.access_key = upbit_access_key
        self.secret_key = upbit_secret_key
        self.server_url = "https://api.upbit.com"
        self.balance = self._get_balance()
        self.KRW, self.BTC = self._get_fiat()

    def create_market_order(self, ticker, order, order_rate):
        if order == 'buy':
            orderbook = self._get_orderbook(ticker)
            price = orderbook[8]['ask_price'] #시장가 매수
            if "BTC-" in ticker:
                volume = float("{:.4f}".format(self.BTC * order_rate / price)) - float(0.0001)
            elif "KRW-" in ticker:
                volume = float("{:.4f}".format(self.KRW * order_rate / price)) - float(0.0001)
            my_order = self._order(ticker=ticker, order='bid', volume=str(volume), price=str(price), ord_type='limit')
            try:
                check_order = self._check_order_is_finished(my_order['uuid'])
                if check_order['state'] == 'wait':
                    self._cancel_order(my_order['uuid'])
                    self.create_market_order(ticker, order, order_rate)
            except KeyError:
                pass
        elif order == 'sell':
            # 시장가매도를 할 일은 없음
            # self._order(ticker=ticker, order='ask', price='null', ord_type='market')
            pass

        self.KRW, self.BTC = self._get_fiat()

    def check_wallet_status(self, currency):
        query = {'currency': currency}
        headers = self._request_headers(query)
        res = requests.get(self.server_url + "/v1/withdraws/chance", params=query, headers=headers)
        state = res.json()
        return state['currency']

    def get_address(self, currency):
        out = self._get_wallet_address(currency)
        try:
            deposit_address = out['deposit_address']
            second_address = out['secondary_address']
        except KeyError:
            time.sleep(10)
            out = self._get_wallet_address(currency)
            deposit_address = out['deposit_address']
            second_address = out['secondary_address']

        return deposit_address, second_address

    def withdrawal(self, currency, amount, address, secondary_address='',transaction_type='internal'):
        query = {
            'currency': currency,
            'amount': amount,
            'address': address,
            "secondary_address" : secondary_address,
            'transaction_type':transaction_type
        }
        headers = self._request_headers(query)
        res = requests.post(self.server_url + "/v1/withdraws/coin", params=query, headers=headers)
        return res.json()

    def _get_wallet_address(self, currency):
        query = {
            'currency': currency,
        }
        headers = self._request_headers(query)
        res = requests.post(self.server_url + "/v1/deposits/generate_coin_address", params=query, headers=headers)
        return res.json()

    def _get_orderbook(self, ticker):
        url = "https://api.upbit.com/v1/orderbook"
        headers = {"markets": ticker}
        response = requests.get(url, params=headers)
        orderbook = response.json()[0]['orderbook_units']
        return orderbook

    def _get_fiat(self):
        KRW = 0
        BTC = 0
        for bal in self.balance:
            if bal['currency'] == 'KRW':
                KRW = int(float((bal['balance'])))
            elif bal['currency'] == 'BTC':
                BTC = float(bal['balance'])
        return KRW, BTC

    def _get_balance(self):
        server_url = 'https://api.upbit.com/v1/accounts'
        headers = self._request_headers()
        res = requests.get(server_url, headers=headers)
        return res.json()

    def _order(self, ticker, order, volume, price, ord_type):
        query = {
            'market': ticker,
            'side': order,
            'volume': volume,
            'price': price,
            'ord_type': ord_type,
        }
        headers = self._request_headers(query)
        res = requests.post(self.server_url + "/v1/orders", params=query, headers=headers)
        return res.json()

    def _check_order_is_finished(self, uuid):
        query = {'uuid':uuid}
        headers = self._request_headers(query)
        res = requests.get(self.server_url + "/v1/order", params=query, headers=headers)
        return res.json()

    def _cancel_order(self, uuid):
        query = {'uuid': uuid}
        headers = self._request_headers(query)
        res = requests.delete(self.server_url + "/v1/order", params=query, headers=headers)

    def _request_headers(self, query=None):
        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4())
        }
        if query is not None:
            m = hashlib.sha512()
            m.update(urlencode(query).encode())
            query_hash = m.hexdigest()
            payload['query_hash'] = query_hash
            payload['query_hash_alg'] = "SHA512"

        jwt_token = jwt.encode(payload, self.secret_key)
        authorize_token = 'Bearer {}'.format(jwt_token)
        headers = {"Authorization": authorize_token}
        return headers

if __name__ == '__main__':
    import csv
    with open('key.txt', 'r') as f:
        keys = list(csv.reader(f, delimiter="/"))
    upbit = Upbit(upbit_access_key=keys[0][1], upbit_secret_key=keys[0][2])
    #upbit.create_market_order('BTC-XRP', 'buy', 1)

    # result=upbit.withdrawal('BTC', address='btc-pizzaday-2021', amount=0.0001)
    # print(result)
    # # wallet 오픈 여부
    state = upbit.check_wallet_status('NKN')
    print(state)
    deposit_address, second_address = upbit.get_address('NKN')
    print(deposit_address, second_address)
