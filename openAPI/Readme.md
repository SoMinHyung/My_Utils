#API
 
### Upbit

1. check_wallet_status : 지갑의 상태를 확인 (deposit, withdraw)
```python
from upbit import Upbit
upbit = Upbit(upbit_access_key, upbit_secret_key)
state = upbit.check_wallet_status('BTC')
#{'code': 'BTC', 'withdraw_fee': '0.0009', 'is_coin': True, 'wallet_state': 'working', 'wallet_support': ['deposit', 'withdraw']}
```

2. get_address : 지갑의 주소를 알아옴. 만약 주소가 발행되지 않았다면, 새로 생성합니다.
```python
from upbit import Upbit
upbit = Upbit(upbit_access_key, upbit_secret_key)
deposit_address, second_address = upbit.get_address('XRP')
print(deposit_address, second_address)
# 입금주소 // tag주소(없다면 None)
```

3. 
