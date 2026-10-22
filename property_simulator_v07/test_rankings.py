import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from providers import ProviderError, completed_bar
from rankings import SEOUL, top20, kr_page, collect_kr, us_universe, daily_us, collect_us, Cancelled
from storage import Store
from sync import SyncManager, validate_options


def chart(symbol='AAPL',price=100,volume=100,stamp=1735813800):
    return {'chart':{'result':[{'meta':{'symbol':symbol,'currency':'USD','instrumentType':'EQUITY','gmtoffset':-18000},'timestamp':[stamp], 'indicators':{'quote':[{'close':[price],'volume':[volume]}]}}]}}


def kr_response(items,total):
    return {'response':{'header':{'resultCode':'00'},'body':{'totalCount':total,'items':{'item':items}}}}


class RankingTests(unittest.TestCase):
    def test_not_volume_ranking(self):
        rows=[dict(symbol='A',close='1',volume='1000'),dict(symbol='B',close='100',volume='100')]
        self.assertEqual(top20(rows)[0]['symbol'],'B')
        self.assertEqual(top20(rows)[0]['amount'],'10000')

    def test_seoul_all_codes(self):
        c=validate_options(dict(start='2000-01-01',end='2025-01-01',seoul_all=True,stocks=False,fx=False))
        self.assertEqual(set(c['regions'].split(',')),set(SEOUL))
        self.assertEqual(len(SEOUL),25)

    def test_kr_day_and_market(self):
        item=dict(basDt='20250102',mrktCtg='KOSDAQ',srtnCd='A035900',clpr='10000',trqu='300',itmsNm='테스트')
        rows,total,n=kr_page(kr_response([item],1),'20250102')
        self.assertEqual(rows[0]['symbol'],'035900.KQ')
        with self.assertRaises(ProviderError):kr_page(kr_response([item],1),'20250103')
        with self.assertRaises(ProviderError):kr_page({'response':{'header':{'resultCode':'30'}}},'20250102')

    def test_kr_complete_pages(self):
        def transport(url,params):
            n=params['pageNo'];start=0 if n==1 else 10
            items=[dict(basDt=params['basDt'],mrktCtg='KOSPI',srtnCd=f'{i:06d}',clpr=str(i+1),trqu='100',itmsNm=f'주식{i}') for i in range(start,start+10)]
            return kr_response(items,20)
        r=collect_kr('dummy',lambda _:None,lambda:False,transport)
        self.assertEqual(r['status'],'complete')
        self.assertEqual(r['top'][0]['symbol'],'000019.KS')
        self.assertEqual(len(r['top']),20)

    def test_universe_filter(self):
        nas='Symbol|Security Name|Test Issue|ETF\nAAPL|Apple Common Stock|N|N\nETF1|Fund|N|Y\nTEST|Test Common Stock|Y|N\nWARR|Some Warrants|N|N\n'
        other='ACT Symbol|Security Name|Exchange|Test Issue|ETF\nBRK.B|Berkshire Common Stock|N|N|N\nPREF|Preferred Stock|N|N|N\nOTC|Other Common Stock|P|N|N\n'
        self.assertEqual([r['symbol'] for r in us_universe(nas,other)],['AAPL','BRK-B'])

    def test_excludes_intraday_and_wrong_target(self):
        p=chart();now=datetime(2025,1,2,18,tzinfo=timezone.utc)
        self.assertIsNone(daily_us(p,now=now))
        now=datetime(2025,1,3,18,tzinfo=timezone.utc)
        self.assertIsNotNone(daily_us(p,'2025-01-02',now))
        self.assertIsNone(daily_us(p,'2025-01-01',now))

    def test_completed_day_requires_close_and_delay(self):
        finish=int(datetime(2025,1,2,21,tzinfo=timezone.utc).timestamp())
        meta={'currentTradingPeriod':{'regular':{'end':finish}}}
        self.assertFalse(completed_bar(date(2025,1,2),meta,datetime(2025,1,2,21,10,tzinfo=timezone.utc),timezone.utc))
        self.assertTrue(completed_bar(date(2025,1,2),meta,datetime(2025,1,2,21,31,tzinfo=timezone.utc),timezone.utc))

    def test_cache_resume_and_partial_label(self):
        symbols=['A'+chr(65+i) for i in range(25)]
        nas=('Symbol|Security Name|Test Issue|ETF\n'+''.join(f'{s}|{s} Common Stock|N|N\n' for s in symbols)).encode()
        other=b'ACT Symbol|Security Name|Exchange|Test Issue|ETF\n'
        calls=[]
        def transport(url,params=None,json_response=True):
            if url.endswith('nasdaqlisted.txt'):return nas
            if url.endswith('otherlisted.txt'):return other
            symbol=url.rsplit('/',1)[-1];calls.append(symbol)
            if symbol=='AA':raise ProviderError('fixture unavailable')
            return chart(symbol,100+len(symbol))
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'x.db')
            r=collect_us(store,lambda _:None,lambda:False,transport)
            self.assertEqual(r['status'],'partial');self.assertEqual(r['missing'],['AA']);self.assertEqual(len(r['top']),20)
            calls.clear();r=collect_us(store,lambda _:None,lambda:False,transport)
            self.assertEqual(set(calls),{'AAPL','MSFT','AA'})
            self.assertEqual(len(store.rank_cache('2025-01-02')),24)

    def test_cancel_does_not_publish_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'x.db')
            with self.assertRaises(Cancelled):collect_kr('test',lambda _:None,lambda:True)
            self.assertEqual(store.ranking(),{})

    def test_fixed_list_never_scans_and_preserves_old_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'x.db');manager=SyncManager(store)
            store.replace('stocks','OLD',[dict(id='OLD',name='Old',date='2025-01-02',currency='KRW',price='10',source='fixture')],{})
            config=validate_options(dict(start='2025-01-01',end='2025-01-03',apartments=False,fx=False,stocks=True,rank_kr=True,rank_us=True,symbols='AAPL,005930.KS'))
            def stock(symbol,start,end):return [dict(id=symbol,name=symbol,date='2025-01-02',currency='KRW',price='10',source='fixture')],{}
            with patch('sync.collect_kr') as kr,patch('sync.collect_us') as us,patch('sync.fetch_stock',side_effect=stock) as fetcher,patch.object(manager.stop,'wait',return_value=False):
                manager.run(config)
                kr.assert_not_called();us.assert_not_called()
                self.assertEqual({call.args[0] for call in fetcher.call_args_list},{'AAPL','005930.KS'})
            self.assertEqual(store.stats()['stocks']['count'],3)
            self.assertEqual(manager.status()['status'],'done')

    def test_watchlist_persistence_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'x.db';store=Store(path)
            self.assertEqual(len(store.watchlist()['items']),17)
            items=store.watchlist()['items']+[dict(id='NFLX',name='넷플릭스')]
            store.watchlist(items)
            self.assertEqual(len(Store(path).catalog()['stocks']),18)
            with self.assertRaises(ValueError):store.watchlist(items+[items[0]])
            self.assertEqual(len(store.watchlist()['items']),18)
            store.watchlist([dict(id='NFLX',name='넷플릭스')])
            self.assertEqual(store.catalog()['stocks'][0]['id'],'NFLX')
            self.assertFalse(store.catalog()['stocks'][0]['available'])

    def test_v02_database_migration_preserves_data(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.db'
            with sqlite3.connect(path) as db:
                db.executescript("CREATE TABLE observations(kind TEXT, scope TEXT, item_id TEXT, date TEXT, data TEXT, PRIMARY KEY(kind,item_id,date)); CREATE TABLE settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);")
                db.execute("INSERT INTO settings VALUES(1,?)", ('{"start":"2000-01-01","symbols":"AAPL"}',))
            store=Store(path)
            store.replace('stocks','AAPL',[dict(id='AAPL',name='Apple',date='2025-01-02',currency='USD',price='100',source='fixture')],{})
            reopened=Store(path)
            self.assertEqual(reopened.stats()['stocks']['count'],1)
            self.assertEqual(reopened.settings()['symbols'],'AAPL')
            self.assertEqual(reopened.ranking(),{})

    def test_old_apartment_cache_and_forced_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'x.db');manager=SyncManager(store);manager.key='fixture'
            store.replace('apartments','11200:202001',[],{})
            config=validate_options(dict(start='2020-01-01',end='2020-01-31',regions='11200',stocks=False,fx=False))
            with patch('sync.fetch_apartments',return_value=([],[],0)) as fetcher, patch.object(manager.stop,'wait',return_value=False):
                manager.run(config)
                fetcher.assert_not_called()
                self.assertEqual(manager.state['reused'],1)
                config['refresh_all']=True
                manager.run(config)
                fetcher.assert_called_once_with('fixture','11200','202001')


if __name__=='__main__':unittest.main()
