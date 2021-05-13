import threading
import os
import time
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

    def check_wallet_status(self, currency):
        query = {
            'currency': currency
        }
        query_string = urlencode(query).encode()
        m = hashlib.sha512()
        m.update(query_string)
        query_hash = m.hexdigest()

        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4()),
            'query_hash': query_hash,
            'query_hash_alg': 'SHA512',
        }

        jwt_token = jwt.encode(payload, self.secret_key)
        authorize_token = 'Bearer {}'.format(jwt_token)
        headers = {"Authorization": authorize_token}

        res = requests.get(self.server_url + "/v1/withdraws/chance", params=query, headers=headers)
        state = res.json()

        return state

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

    def _get_wallet_address(self, currency):
        query = {
            'currency': currency,
        }
        query_string = urlencode(query).encode()

        m = hashlib.sha512()
        m.update(query_string)
        query_hash = m.hexdigest()

        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4()),
            'query_hash': query_hash,
            'query_hash_alg': 'SHA512',
        }

        jwt_token = jwt.encode(payload, self.secret_key)
        authorize_token = 'Bearer {}'.format(jwt_token)
        headers = {"Authorization": authorize_token}

        res = requests.post(self.server_url + "/v1/deposits/generate_coin_address", params=query, headers=headers)

        return res.json()


if __name__ == '__main__':
    checker = Upbit()
    out = checker.get_address('STPT')
    print(out)