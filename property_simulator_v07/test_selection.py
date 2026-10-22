import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from providers import fetch_apartments, apartment_rows, MOLIT_OFFI
from housing import match_target, APARTMENT_REGIONS
from storage import Store
from sync import SyncManager, validate_options
from test_collectors import trade, xml


class SelectionTests(unittest.TestCase):
    def test_missing_new_listings_do_not_skip_next_stock(self):
        from providers import ProviderError
        with tempfile.TemporaryDirectory() as tmp:
            manager=SyncManager(Store(Path(tmp)/'db'))
            config=validate_options(dict(start='2020-01-01',end='2020-01-31',apartments=False,fx=False,symbols='SKHY,SNDK,SPCX,TSLA'))
            def collect(symbol,*args):
                if symbol != 'TSLA':
                    raise ProviderError('상장 전 날짜')
                return [dict(id='TSLA',name='테슬라',currency='USD',date='2020-01-02',price='100',source='fixture')],{}
            with patch('sync.fetch_stock',side_effect=collect) as fetcher, patch.object(manager.stop,'wait',return_value=False):
                manager.run(config)
            self.assertEqual(fetcher.call_count,4)
            self.assertEqual(manager.store.stats()['stocks']['count'],1)
            self.assertEqual(manager.state['status'],'partial')

    def test_district_and_phase_matching(self):
        self.assertTrue(match_target('11680','압구정동','현대1차(12,13,21,22,31,32,33동)'))
        self.assertTrue(match_target('11680','도곡동','타워팰리스3'))
        self.assertTrue(match_target('11650','반포동','래미안 원베일리'))
        self.assertFalse(match_target('11680','논현동','현대1차'))
        self.assertFalse(match_target('11680','압구정동','현대빌라'))
        self.assertFalse(match_target('11710','신천동','롯데월드타워앤드롯데월드몰'))

    def test_officetel_endpoint_filter_and_cancellations(self):
        row={**trade(), 'offiNm':'롯데월드타워앤드롯데월드몰','umdNm':'신천동','jibun':'29','sggCd':'11710'}
        row.pop('aptNm')
        calls=[]
        def transport(url, params, **kwargs):
            calls.append(url)
            return xml([row,{**row,'cdealType':'O','dealAmount':'99,999'},{**row,'offiNm':'다른오피스텔'}])
        rows,raw,cancelled=fetch_apartments('fixture','11710','202501',transport,property_type='officetel',selected_only=True)
        self.assertEqual(calls,[MOLIT_OFFI])
        self.assertEqual(len(rows),1)
        self.assertEqual(cancelled,1)
        self.assertEqual(len(raw),2)
        self.assertEqual(rows[0]['price_krw'],'100000000')
        self.assertTrue(rows[0]['id'].startswith('OFFI_'))

    def test_legacy_database_filters_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            rows,_=apartment_rows([trade(),{**trade(),'aptNm':'다른아파트'}],'11200','202501')
            for row in rows:
                for field in ('property_type','region_code','town','building_name'):
                    row.pop(field)
            store.replace('apartments','11200:202501',rows,{})
            with store.connect() as db:
                db.execute('CREATE TABLE watchlist_config(id INTEGER PRIMARY KEY,data TEXT)')
                db.execute('INSERT INTO watchlist_config VALUES(1,?)',(json.dumps([{'id':'OLD','name':'Old','currency':'USD'}]),))
            self.assertEqual(len(store.catalog()['apartments']),1)
            self.assertEqual(store.stats()['apartments']['count'],2)
            self.assertEqual(len(store.watchlist()['items']),17)
            data=store.comparison(dict(house_date='2025-01-31',stock_date='2025-01-31'))
            self.assertEqual(len(data['apartments']),1)
            self.assertEqual(sum(x['count'] for x in store.catalog()['housing_status']),1)

    def test_production_start_overrides_legacy_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=SyncManager(Store(Path(tmp)/'db'))
            with patch('sync.threading.Thread'):
                manager.start(dict(start='2025-01-01',end='2025-01-31',regions='11110',symbols='OLD',seoul_all=True))
            config=manager.store.settings()
            self.assertEqual(set(config['regions'].split(',')),set(APARTMENT_REGIONS))
            self.assertEqual(len(config['symbols'].split(',')),17)
            self.assertIn('SKHY',config['symbols'].split(','))
            self.assertFalse(config['seoul_all'])
            self.assertEqual(config['selection_version'],5)

    def test_four_districts_plus_officetel_and_independent_failures(self):
        from providers import ProviderError
        with tempfile.TemporaryDirectory() as tmp:
            manager=SyncManager(Store(Path(tmp)/'db'));manager.key='fixture'
            config=validate_options(dict(start='2025-01-01',end='2025-01-31',regions=','.join(APARTMENT_REGIONS),stocks=False,fx=False))
            with patch('sync.fetch_apartments',return_value=([],[],0)) as apt, patch('sync.fetch_officetels',side_effect=ProviderError('approval required')) as offi, patch.object(manager.stop,'wait',return_value=False):
                manager.run(config)
            self.assertEqual(apt.call_count,4)
            offi.assert_called_once_with('fixture','11710','202501')
            self.assertEqual(manager.state['status'],'partial')
            self.assertEqual(manager.state['successes'],4)


if __name__=='__main__':
    unittest.main()
