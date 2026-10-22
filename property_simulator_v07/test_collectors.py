import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from providers import (parse_molit, apartment_rows, fetch_apartments, parse_yahoo, parse_fx, ProviderError)
from storage import Store
from sync import SyncManager, months, validate_options


def trade(price='10,000', cancelled='', day='15'):
    return dict(aptNm='트리마제', umdNm='성수동1가', jibun='1-1', sggCd='11200', excluUseAr='84.00',
                dealYear='2025', dealMonth='1', dealDay=day, dealAmount=price, cdealType=cancelled, cdealDay='')


def xml(items, total=None):
    body=''.join('<item>'+''.join(f'<{k}>{v}</{k}>' for k,v in i.items())+'</item>' for i in items)
    return f'<response><header><resultCode>000</resultCode></header><body><items>{body}</items><totalCount>{len(items) if total is None else total}</totalCount></body></response>'.encode()


class CollectorTests(unittest.TestCase):
    def test_cancelled_and_median(self):
        rows, cancelled = apartment_rows([trade('10,000'),trade('20,000'),trade('99,999','O')], '11200','202501')
        self.assertEqual(cancelled,1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['price_krw'],'150000000')
        self.assertIn('2건 중위값',rows[0]['source'])

    def test_identity_separates_area_and_address(self):
        items=[trade(),{**trade(), 'excluUseAr':'59'}, {**trade(),'jibun':'2-2'}]
        rows,_=apartment_rows(items,'11200','202501')
        self.assertEqual(len({r['id'] for r in rows}),3)

    def test_api_auth_error_not_exposed(self):
        with self.assertRaises(ProviderError) as cm:
            parse_molit(b'<response><header><resultCode>30</resultCode><resultMsg>secret-token</resultMsg></header></response>')
        self.assertNotIn('secret-token',str(cm.exception))

    def test_pagination(self):
        calls=[]
        def transport(url,params,**kwargs):
            calls.append(params['pageNo'])
            return xml([trade(day=str(params['pageNo']))],2)
        rows,raw,_=fetch_apartments('test','11200','202501',transport)
        self.assertEqual(calls,[1,2])
        self.assertEqual(len(rows),2)
        self.assertEqual(len(raw),2)

    def test_incomplete_page_does_not_succeed(self):
        def transport(url,params,**kwargs):
            return xml([trade()] if params['pageNo']==1 else [],2)
        with self.assertRaises(ProviderError):
            fetch_apartments('test','11200','202501',transport)

    def test_same_page_rejected(self):
        with self.assertRaises(ProviderError):
            fetch_apartments('test','11200','202501',lambda *a,**k:xml([trade()],2))

    def test_missing_field_fails_closed(self):
        row=trade();del row['excluUseAr']
        with self.assertRaises(ProviderError):
            apartment_rows([row],'11200','202501')

    def test_splits_after_requested_end_restored(self):
        def ts(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())
        payload={'chart':{'result':[{'meta':{'currency':'USD','instrumentType':'EQUITY','gmtoffset':0},
                  'timestamp':[ts('2020-08-28'),ts('2020-08-31')],
                  'indicators':{'quote':[{'close':[125,125]}]},
                  'events':{'splits':{'one':{'date':ts('2020-08-31'),'numerator':4,'denominator':1}}}}]}}
        rows=parse_yahoo(payload,'AAPL',date(2020,8,28),date(2020,8,28),datetime(2025,1,1,tzinfo=timezone.utc))
        self.assertEqual(rows[0]['price'],'500')
        rows=parse_yahoo(payload,'AAPL',date(2020,8,31),date(2020,8,31),datetime(2025,1,1,tzinfo=timezone.utc))
        self.assertEqual(rows[0]['price'],'125')

    def test_fx_direction_and_range(self):
        rows=parse_fx({'base':'USD','rates':{'2025-01-01':{'KRW':1400},'2024-12-31':{'KRW':1300}}},date(2025,1,1),date(2025,1,2))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['krw_per_usd'],'1400')
        with self.assertRaises(ProviderError):
            parse_fx({'base':'KRW','rates':{}},date(2025,1,1),date(2025,1,2))

    def test_year_range(self):
        self.assertEqual(list(months(date(2000,1,1),date(2006,2,1))),['200601','200602'])
        self.assertEqual(list(months(date(2000,1,1),date(2005,12,31))),[])
        with self.assertRaises(ValueError):
            validate_options({'start':'1999-01-01','end':'2025-01-01'})

    def test_store_refresh_removes_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db')
            rows,_=apartment_rows([trade()],'11200','202501')
            store.replace('apartments','11200:202501',rows,{'items':[trade()]})
            self.assertEqual(store.stats()['apartments']['count'],1)
            store.replace('apartments','11200:202501',[],{'items':[trade(cancelled='O')]})
            self.assertEqual(store.stats()['apartments']['count'],0)

    def test_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db');rows,_=apartment_rows([trade()],'11200','202501')
            store.replace('apartments','11200:202501',rows,{})
            with self.assertRaises(KeyError):
                store.replace('apartments','11200:202501',[{}],{})
            self.assertEqual(store.stats()['apartments']['count'],1)

    def test_range_refresh_preserves_other_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db')
            rows=[dict(id='AAPL',date=d,price='100',name='Apple',currency='USD',source='test') for d in ['2024-01-02','2025-01-02']]
            store.replace('stocks','AAPL',rows,{})
            store.replace('stocks','AAPL',[],{},'2025-01-01','2025-01-31')
            self.assertEqual([r['date'] for r in store.read()['stocks']],['2024-01-02'])
            store.settings({'api_key':'secret','auto':False})
            self.assertNotIn('secret',json.dumps(store.settings()))

    def test_job_without_key_is_partial_and_saves_stocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db');manager=SyncManager(store);manager.key=''
            config=validate_options(dict(start='2025-01-01',end='2025-01-02',regions='11200',symbols='AAPL',fx=False))
            row=dict(id='AAPL',name='Apple',currency='USD',date='2025-01-02',price='100',source='test')
            with patch('sync.fetch_stock',return_value=([row],{})):
                manager.run(config)
            self.assertEqual(manager.status()['status'],'partial')
            self.assertEqual(store.stats()['stocks']['count'],1)
            self.assertIn('인증키',manager.status()['errors'][0])

    def test_saved_dataset_http_path(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from app import Handler
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'market.db')
            rows,_=apartment_rows([trade()],'11200','202501')
            store.replace('apartments','11200:202501',rows,{})
            store.replace('stocks','TEST.KS',[dict(id='TEST.KS',name='Test',currency='KRW',date='2025-01-15',price='10000',source='test')],{})
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.manager=SyncManager(store);server.manager.key='never-return-this-key'
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/api/sync/status') as response:
                    text=response.read().decode();self.assertNotIn('never-return-this-key',text)
                req=Request(base+'/api/catalog',data=b'{"saved": true}',headers={'Content-Type':'application/json'})
                with urlopen(req) as response:
                    result=json.loads(response.read());self.assertEqual(len(result['apartments']),1)
                payload=dict(saved=True,apartment=rows[0]['id'],stock='TEST.KS',house_date='2025-01-15',stock_date='2025-01-15',quantity=1,holdings=[dict(id='NOT_LISTED',quantity=0)])
                req=Request(base+'/api/calculate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urlopen(req) as response:
                    result=json.loads(response.read());self.assertEqual(result['whole_shares'],10000)
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main(verbosity=2)
