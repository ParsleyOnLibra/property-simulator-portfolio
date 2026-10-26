"""백그라운드 수집, 진행률, 중지 및 실행 중 하루 한 번 갱신."""
import copy
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from providers import ProviderError, fetch_apartments, fetch_stock, fetch_fx
from rankings import SEOUL, collect_kr, collect_us, Cancelled


def validate_options(value):
    try:
        start = date.fromisoformat(value['start'])
        end = date.fromisoformat(value['end'])
        if not date(2000, 1, 1) <= start <= end <= date.today():
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValueError('수집 날짜는 2000-01-01부터 오늘 사이이며 시작일 ≤ 종료일이어야 합니다.') from None
    regions = list(dict.fromkeys(x.strip() for x in str(value.get('regions', '')).split(',') if x.strip()))
    if value.get('seoul_all'):
        regions = list(SEOUL)
    symbols = list(dict.fromkeys(x.strip().upper() for x in str(value.get('symbols', '')).split(',') if x.strip()))
    apt, stocks, fx = (bool(value.get(k, True)) for k in ('apartments', 'stocks', 'fx'))
    if apt and (not 1 <= len(regions) <= 25 or any(not re.fullmatch(r'\d{5}', x) for x in regions)):
        raise ValueError('지역코드 5자리를 쉼표로 구분해 1~25개 입력하세요.')
    if stocks and not (value.get('rank_kr') or value.get('rank_us')) and (not 1 <= len(symbols) <= 40 or any(not re.fullmatch(r'(?:\d{6}\.(?:KS|KQ)|[A-Z][A-Z0-9.-]{0,14})', x) for x in symbols)):
        raise ValueError('종목 코드를 쉼표로 구분해 1~40개 입력하세요. 예: 005930.KS,AAPL')
    if not any((apt, stocks, fx)):
        raise ValueError('수집할 자료를 하나 이상 선택하세요.')
    return dict(start=start.isoformat(), end=end.isoformat(), regions=','.join(regions), symbols=','.join(symbols),
                apartments=apt, stocks=stocks, fx=fx, auto=bool(value.get('auto', False)),
                seoul_all=bool(value.get('seoul_all',False)),rank_kr=bool(value.get('rank_kr',False)),rank_us=bool(value.get('rank_us',False)),refresh_all=bool(value.get('refresh_all',False)))


def months(start, end):
    cursor = date(max(2006, start.year), start.month if start.year >= 2006 else 1, 1)
    while cursor <= end:
        yield cursor.strftime('%Y%m')
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)


class SyncManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.shutdown = threading.Event()
        self.key = os.environ.get('DATA_GO_KR_KEY', '').strip()
        key_path = self.store.path.parent / 'api_key.txt'
        if not self.key and key_path.exists():
            self.key = key_path.read_text(encoding='utf-8-sig').strip()
        self.state = {'status': 'idle', 'message': '수집 대기', 'completed': 0, 'total': 0, 'errors': [], 'successes': 0}

    def status(self):
        with self.lock:
            return {**copy.deepcopy(self.state), 'has_key': bool(self.key), 'stats': self.store.stats(), 'settings': self.store.settings(), 'rankings': self.store.ranking()}

    def start(self, options, api_key=None):
        config = validate_options(options)
        with self.lock:
            if self.state['status'] == 'running':
                raise ValueError('이미 수집 중입니다. 완료 또는 중지 후 다시 시도하세요.')
            if api_key:
                if not isinstance(api_key, str) or len(api_key) > 1000:
                    raise ValueError('인증키 형식을 확인하세요.')
                self.key = api_key.strip()
            config['last_attempt'] = time.time()
            self.store.settings(config)
            self.stop.clear()
            self.state = {'status': 'running', 'message': '수집 준비 중', 'completed': 0, 'total': 0, 'errors': [], 'successes': 0}
            threading.Thread(target=self.run, args=(config,), daemon=True).start()
        return self.status()

    def cancel(self):
        self.stop.set()
        return self.status()

    def run(self, config):
        start, end = date.fromisoformat(config['start']), date.fromisoformat(config['end'])
        units = []
        symbols = set()
        rank_tasks = [(m,fn) for m,fn in [('KR',collect_kr),('US',collect_us)] if config['stocks'] and config.get('rank_'+m.lower())]
        with self.lock:
            self.state['total'] = len(rank_tasks)
        def progress(message):
            with self.lock:
                self.state['message'] = message
        for market,collector in rank_tasks:
            try:
                result = (collector(self.key,progress,self.stop.is_set) if market=='KR' else
                          collector(self.store,progress,self.stop.is_set,refresh=config.get('refresh_all',False)))
                if self.stop.is_set():
                    raise Cancelled()
                self.store.ranking(market,result)
                symbols.update(r['symbol'] for r in result['top'])
                with self.lock:
                    self.state['successes'] += 1
                    if result['status']!='complete':
                        self.state['errors'].append(f'{market} 순위: {len(result["missing"])}종목 미확보. 전체 시장 확정 순위가 아니라 확보한 종목 내 순위입니다.')
            except Cancelled:
                with self.lock:
                    self.state.update(status='cancelled',message='수집 중지 · 순위 캐시와 기존 자료는 보존됩니다.')
                return
            except (ProviderError,ValueError) as exc:
                with self.lock:
                    self.state['errors'].append(f'{market} 순위: {exc}')
            except Exception:
                with self.lock:
                    self.state['errors'].append(f'{market} 순위 처리 오류. 기존 순위와 DB는 보존됩니다.')
            with self.lock:
                self.state['completed'] += 1
        if config['stocks']:
            if not rank_tasks:
                symbols.update(filter(None,config['symbols'].split(',')))
            units.extend(('stocks', symbol, start, end) for symbol in sorted(symbols))
        if config['fx']:
            units.extend(('fx', str(year), max(start, date(year, 1, 1)), min(end, date(year, 12, 31))) for year in range(start.year, end.year + 1))
        skipped = 0
        if config['apartments']:
            if self.key:
                today = date.today()
                recent_cutoff = today.year*12+today.month-6
                for code in config['regions'].split(','):
                    for month in months(start,end):
                        scope = code+':'+month
                        old = int(month[:4])*12+int(month[4:]) < recent_cutoff
                        if old and not config.get('refresh_all') and self.store.has_scope('apartments',scope):
                            skipped += 1
                            continue
                        units.append(('apartments',scope,start,end))
            else:
                with self.lock:
                    self.state['errors'].append('아파트: 인증키가 없어 수집하지 않았습니다. 앱에 키를 입력한 뒤 다시 수집하세요.')
        with self.lock:
            self.state['total'] += len(units)
            self.state['reused'] = skipped
        failures = {'apartments': 0, 'stocks': 0, 'fx': 0}
        try:
            for kind, scope, begin, finish in units:
                if self.stop.is_set():
                    break
                if failures[kind] >= 3:
                    with self.lock:
                        self.state['completed'] += 1
                    continue
                with self.lock:
                    self.state['message'] = f'{kind} · {scope} 수집 중'
                try:
                    if kind == 'apartments':
                        region, month = scope.split(':')
                        rows, raw, cancelled = fetch_apartments(self.key, region, month)
                        if self.stop.is_set():
                            break
                        self.store.replace(kind, scope, rows, {'items': raw, 'cancelled': cancelled})
                    elif kind == 'stocks':
                        rows, raw = fetch_stock(scope, begin, finish)
                        if self.stop.is_set():
                            break
                        self.store.replace(kind, scope, rows, raw, begin.isoformat(), finish.isoformat())
                    else:
                        rows, raw = fetch_fx(begin, finish)
                        if self.stop.is_set():
                            break
                        self.store.replace(kind, scope, rows, raw, begin.isoformat(), finish.isoformat())
                    failures[kind] = 0
                    with self.lock:
                        self.state['successes'] += 1
                except (ProviderError, ValueError) as exc:
                    failures[kind] += 1
                    with self.lock:
                        if len(self.state['errors']) < 50:
                            self.state['errors'].append(f'{kind} {scope}: {exc}')
                        if failures[kind] == 3:
                            self.state['errors'].append(f'{kind}: 연속 3회 실패하여 나머지 요청을 건너뜁니다.')
                with self.lock:
                    self.state['completed'] += 1
                if self.stop.wait(.15):
                    break
            with self.lock:
                if self.stop.is_set():
                    self.state.update(status='cancelled', message='수집 중지 · 완료된 단위는 저장되어 있습니다.')
                elif self.state['errors']:
                    self.state.update(status='partial' if self.state['successes'] else 'failed', message='일부 또는 전체 수집 실패 · 오류와 저장 범위를 확인하세요.')
                else:
                    self.state.update(status='done', message='요청한 수집 완료 · 자료가 없는 날짜는 생성하지 않았습니다.')
        except Exception:
            # 예상치 못한 예외에서도 키/URL/응답은 반환하지 않음.
            with self.lock:
                self.state.update(status='failed', message='저장 또는 처리 오류로 중단되었습니다. 기존 DB는 보존됩니다.')

    def scheduler(self):
        while not self.shutdown.wait(10):
            config = self.store.settings()
            if config and config.get('auto') and time.time() - config.get('last_attempt', 0) >= 86400:
                with self.lock:
                    busy = self.state['status'] == 'running'
                if not busy:
                    config['end'] = date.today().isoformat()
                    try:
                        self.start(config)
                    except ValueError:
                        pass

    def launch_scheduler(self):
        threading.Thread(target=self.scheduler, daemon=True).start()
