import json
import tempfile
import threading
import unittest
from pathlib import Path
from decimal import Decimal
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pricing import compare_price
from storage import Store
from sync import SyncManager
from app import Handler
from test_collectors import trade
from providers import apartment_rows

class PriceInputTests(unittest.TestCase):
    def setUp(self):
        self.house=dict(id='HOME',date='2025-01-15',price_krw=Decimal('1000000000'),name='테스트',area_m2=84,source='fixture')
        self.payload=dict(apartment='HOME',house_date='2025-01-31',input_price='100000')
    def test_house_divided_by_entered_krw_price(self):
        r=compare_price(self.payload,{'apartments':[self.house]},dict(id='005930.KS',name='삼성전자',currency='KRW'))
        self.assertEqual(r['shares'],10000)
        self.assertTrue(r['price_input'])
        self.assertEqual(r['stock']['date'],'사용자 입력')
    def test_dollar_price_and_explicit_fx(self):
        r=compare_price({**self.payload,'input_price':'100','input_fx':'1250'},{'apartments':[self.house]},dict(id='NVDA',name='엔비디아',currency='USD'))
        self.assertEqual(r['shares'],8000)
        self.assertEqual(r['price_krw'],125000)
        with self.assertRaises(ValueError):compare_price(self.payload,{'apartments':[self.house]},dict(currency='USD'))
    def test_invalid_prices_and_no_future_house(self):
        for value in ['0','-1','NaN','Infinity','']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                compare_price({**self.payload,'input_price':value},{'apartments':[self.house]},dict(currency='KRW'))
        with self.assertRaises(ValueError):
            compare_price({**self.payload,'house_date':'2025-01-01'},{'apartments':[self.house]},dict(currency='KRW'))
    def test_saved_http_history_and_price_without_stored_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db');rows,_=apartment_rows([trade()],'11200','202501')
            store.replace('apartments','11200:202501',rows,{})
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.manager=SyncManager(store)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def post(path,payload):
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})) as response:return json.load(response)
            try:
                payload=dict(saved=True,stock='005930.KS',stock_date='2025-01-31',house_date='2025-01-31',apartment=rows[0]['id'],input_price='10000')
                self.assertEqual(post('/api/stock-history',payload)['rows'],[])
                result=post('/api/price-compare',payload)
                self.assertEqual(result['shares'],10000)
                with self.assertRaises(HTTPError):post('/api/price-compare',{**payload,'stock':'NOT_REGISTERED'})
                stockrows=[dict(id='005930.KS',name='삼성전자',currency='KRW',date=d,price=p,source='fixture') for d,p in [('2025-01-02','10000'),('2025-01-03','12000')]]
                store.replace('stocks','005930.KS',stockrows,{})
                self.assertEqual([x['price'] for x in post('/api/stock-history',payload)['rows']],[10000,12000])
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
