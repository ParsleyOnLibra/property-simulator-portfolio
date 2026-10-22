import json
import threading
import unittest
import urllib.request
import urllib.error
from decimal import Decimal
from pathlib import Path
from http.server import ThreadingHTTPServer
from core import calculate, dataset, latest, metrics, number, quote
from app import Handler

ROOT = Path(__file__).parent


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.raw = {k: (ROOT / 'data' / f'{k}.csv').read_text(encoding='utf-8') for k in ('apartments', 'stocks', 'fx')}
        self.data = dataset(self.raw)
        self.payload = dict(data=self.raw, apartment='DEMO_SEOUL_84', stock='DEMO_US',
                            house_date='2025-01-15', stock_date='2025-01-15', quantity='1000',
                            holdings=[{'id': 'DEMO_KR', 'quantity': '4000'}, {'id': 'DEMO_US', 'quantity': '100'}])

    def test_usd_conversion_and_portfolio(self):
        r = calculate(self.payload)
        self.assertEqual(r['price_krw'], 280000)
        self.assertEqual(r['whole_shares'], 4286)
        self.assertEqual(r['value'], 280000000)
        self.assertEqual(r['portfolio_total'], 428000000)
        self.assertEqual([a['id'] for a in r['affordable']], ['DEMO_SUWON_59'])

    def test_independent_dates(self):
        self.payload.update(house_date='2010-01-15', stock_date='2025-01-15')
        r = calculate(self.payload)
        self.assertEqual(r['house']['price_krw'], 300000000)
        self.assertEqual(r['whole_shares'], 1072)
        self.payload.update(house_date='2025-01-15', stock_date='2010-01-15')
        self.assertEqual(calculate(self.payload)['price_krw'], 22000)

    def test_no_future_observation(self):
        self.assertEqual(latest(self.data['stocks'], '2024-12-31', 'DEMO_US')['date'], '2020-01-15')
        with self.assertRaises(ValueError):
            latest(self.data['apartments'], '2009-01-01', 'DEMO_SEOUL_84')

    def test_fx_uses_actual_stock_date(self):
        self.data['fx'].append({'date': '2025-01-16', 'krw_per_usd': Decimal(9999), 'source': 'test'})
        r = quote(self.data, 'DEMO_US', '2025-01-17')
        self.assertEqual(r['price_krw'], 280000)
        self.assertEqual(r['fx']['date'], '2025-01-15')

    def test_missing_fx_is_not_silently_one(self):
        self.data['fx'] = []
        with self.assertRaises(ValueError):
            quote(self.data, 'DEMO_US', '2025-01-15')
        self.assertEqual(quote(self.data, 'DEMO_KR', '2025-01-15')['price_krw'], 100000)

    def test_invalid_numbers(self):
        for value in ('NaN', 'Infinity', '-1', '0', 'abc', '1e19'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value, '가격')

    def test_duplicate_and_metadata(self):
        self.raw['stocks'] += self.raw['stocks'].splitlines()[1] + '\n'
        with self.assertRaises(ValueError):
            dataset(self.raw)
        self.setUp()
        self.raw['apartments'] = self.raw['apartments'].replace('84,2015', '59,2015', 1)
        with self.assertRaises(ValueError):
            dataset(self.raw)

    def test_ceil_decimal_exactness(self):
        self.assertEqual(metrics('0.3', '0.1', '0')['whole_shares'], 3)
        self.assertEqual(metrics('301', '100', '0')['whole_shares'], 4)
        self.assertEqual(metrics('300', '100', '4')['shortfall'], 0)

    def test_manual_mode(self):
        r = calculate(dict(mode='manual', currency='USD', house_price='1200000000',
                           price='200', fx='1400', quantity='1000',
                           house_date='2025-01-15', stock_date='2010-01-15'))
        self.assertEqual(r['price_krw'], 280000)
        self.assertEqual(r['whole_shares'], 4286)

    def test_bom_and_bad_headers(self):
        self.raw['stocks'] = '\ufeff' + self.raw['stocks']
        dataset(self.raw)
        self.raw['fx'] = 'date,rate,source\n'
        with self.assertRaises(ValueError):
            dataset(self.raw)

    def test_stale_values_exposed(self):
        self.payload.update(house_date='2025-02-15', stock_date='2025-02-15')
        r = calculate(self.payload)
        self.assertEqual(len(r['warnings']), 2)
        self.assertIn('31', r['warnings'][0])

    def test_http_round_trip(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base + '/') as response:
                self.assertIn('집의 다른 단위', response.read().decode())
            for route, payload in [('/api/catalog', {'data': self.raw}), ('/api/calculate', self.payload)]:
                request = urllib.request.Request(base + route, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.loads(response.read()))
            request = urllib.request.Request(base + '/api/calculate', data=b'{}', headers={'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(request)
            self.assertEqual(cm.exception.code, 400)
            self.assertIn('error', json.loads(cm.exception.read()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
