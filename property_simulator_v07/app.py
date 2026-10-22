"""실행: python app.py — Python 표준 라이브러리만 사용."""
import argparse
import json
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core import SCHEMAS, calculate, dataset
from storage import Store
from sync import SyncManager
from pricing import compare_price

ROOT = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def manager(self):
        return self.server.manager

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False,
                              default=lambda v: float(v) if isinstance(v, Decimal) else str(v)).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/':
            self.send(200, (ROOT / 'static/index.html').read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/api/demo':
            self.send(200, {k: (ROOT / 'data' / f'{k}.csv').read_text(encoding='utf-8') for k in SCHEMAS})
        elif self.path == '/api/watchlist':
            self.send(200,self.manager().store.watchlist())
        elif self.path == '/api/health':
            self.send(200, {'ok': True})
        elif self.path == '/api/sync/status':
            self.send(200, self.manager().status())
        elif self.path == '/favicon.ico':
            self.send(204, b'')
        else:
            self.send(404, {'error': '없는 경로입니다.'})

    def do_POST(self):
        # 로컬 브라우저의 다른 사이트가 API에 접근하지 않도록 same-origin 제한.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.send(403, {'error': '허용되지 않은 출처입니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8_000_000:
                raise ValueError('요청 크기는 8MB 이하여야 합니다.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('JSON 객체가 필요합니다.')
            if self.path == '/api/watchlist':
                if self.manager().status()['status']=='running':
                    raise ValueError('수집 중에는 목록을 바꿀 수 없습니다. 수집 완료 또는 중지 후 저장하세요.')
                result=self.manager().store.watchlist(payload.get('items'))
            elif self.path in ('/api/stock-history','/api/price-compare'):
                if payload.get('saved'):
                    stock=next((x for x in self.manager().store.watchlist()['items'] if x['id']==payload.get('stock')),None)
                    if not stock:
                        raise ValueError('등록된 종목을 선택하세요.')
                    if self.path=='/api/stock-history':
                        result={**self.manager().store.stock_history(stock['id']),'stock':stock}
                    else:
                        result=compare_price(payload,self.manager().store.comparison(payload),stock)
                else:
                    data=dataset(payload.get('data'))
                    rows=[r for r in data['stocks'] if r['id']==payload.get('stock')]
                    stock=rows[-1] if rows else None
                    if self.path=='/api/stock-history':
                        result=dict(stock=stock,rows=rows,fx=data['fx'][-1] if data['fx'] else None)
                    else:
                        result=compare_price(payload,data,stock)
            elif self.path == '/api/calculate':
                result = calculate(payload, self.manager().store.comparison(payload) if payload.get('saved') and payload.get('mode')!='manual' else None)
            elif self.path == '/api/catalog':
                if payload.get('saved'):
                    return self.send(200, self.manager().store.catalog())
                data = dataset(payload.get('data'))
                result = {k: list({r['id']: r for r in data[k]}.values()) for k in ('apartments', 'stocks')}
                result['ranges'] = {k: {'start': v[0]['date'], 'end': v[-1]['date'], 'count': len(v)} if v else None for k, v in data.items()}
            elif self.path == '/api/sync/start':
                result = self.manager().start(payload, payload.get('api_key'))
            elif self.path == '/api/sync/cancel':
                result = self.manager().cancel()
            elif self.path == '/api/sync/auto-off':
                config = self.manager().store.settings()
                if config:
                    config['auto'] = False
                    self.manager().store.settings(config)
                result = self.manager().status()
            else:
                return self.send(404, {'error': '없는 API입니다.'})
            self.send(200, result)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.send(400, {'error': str(exc)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        server.manager = SyncManager(Store(ROOT / 'local_data' / 'market.sqlite3'))
        server.manager.launch_scheduler()
    except OSError as exc:
        raise SystemExit(f'실행 실패: {exc}\n다른 포트로 실행하세요: python app.py --port 8001')
    print(f'부동산 × 주식 시뮬레이터: http://127.0.0.1:{args.port}', flush=True)
    print('종료: Ctrl+C / 이 서버는 내 컴퓨터에서만 접속 가능합니다.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.stop.set()
        server.manager.shutdown.set()
        server.server_close()
