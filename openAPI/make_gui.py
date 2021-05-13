import time
import sys
import getopt
import csv
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5 import uic

from upbit import Upbit
from binance import Binance

form_class = uic.loadUiType("untitled.ui")[0]

class MyWindow(QMainWindow, form_class):
    def __init__(self, ticker):
        super().__init__()
        self.setupUi(self)
        self.timer = QTimer(self)
        self.timer.start(3000)
        self.timer.timeout.connect(self.check)

        with open('key.txt','r') as f:
            keys = list(csv.reader(f, delimiter="/"))

        self.upbit = Upbit(upbit_access_key=keys[0][1], upbit_secret_key=keys[0][2])
        self.binance = Binance(binance_access_key=keys[1][1], binance_secret_key=keys[1][2])
        self.ticker = ticker

    def check(self):
        cur_time = QTime.currentTime()
        str_time = cur_time.toString('hh:mm:ss')
        self.statusBar().showMessage(str_time)

        status = self.upbit.check_wallet_status(self.ticker)
        print(status['currency'])
        wallet_status_list = status['currency']['wallet_support']
        wallet_status = ''
        for ls in wallet_status_list:
            wallet_status += ls
            wallet_status += '\n'
        self.textBrowser.setText(wallet_status)

        if 'deposit' in wallet_status:
            # self.binance.create_market_order('XRP/BTC','buy',0.005)
            first_address, second_address = self.upbit.get_address(self.ticker)
            print(first_address)
            print(second_address)



            QCoreApplication.quit()



if __name__ == "__main__":
    opts, args = getopt.getopt(sys.argv[1:], "t:")
    kwargs = {}
    for o, a in opts:
        if o in ('-t', '--ticker'):
            kwargs["ticker"] = a

    # kwargs['ticker'] = 'SNX'
    # Main process
    app = QApplication(sys.argv)
    mywindow = MyWindow(**kwargs)
    mywindow.show()
    app.exec_()