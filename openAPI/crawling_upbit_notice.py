import csv
import json
import requests
import random
import time
from telegram_bot import Telegram_bot

bot = Telegram_bot()

newest_news = {}
count = 0
while True:
    count +=1
    req = requests.get('https://api-manager.upbit.com/api/v1/notices?page=1&per_page=20&thread_name=general')
    crawled_data = req.json()
    time.sleep(random.uniform(2,4))

    new = crawled_data['data']['list'][0]
    if count == 1:
        newest_news = new
    print(count)
    print(new['title'])
    if newest_news['title'] != new['title']:
        bot.sendMessage(msg=new)
        newest_news = new