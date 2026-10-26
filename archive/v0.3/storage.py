"""SQLite 영구 저장. 수집 단위(지역/월, 종목, 환율/년)마다 원자적으로 교체."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS observations (
                kind TEXT NOT NULL, scope TEXT NOT NULL, item_id TEXT NOT NULL,
                date TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(kind,item_id,date));
            CREATE TABLE IF NOT EXISTS snapshots (
                kind TEXT NOT NULL, scope TEXT NOT NULL, fetched_at TEXT NOT NULL,
                raw TEXT NOT NULL, PRIMARY KEY(kind,scope));
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rankings (market TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rank_cache (date TEXT, symbol TEXT, data TEXT NOT NULL, PRIMARY KEY(date,symbol));
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def replace(self, kind, scope, rows, raw, start=None, end=None):
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if start and end:
                db.execute('DELETE FROM observations WHERE kind=? AND scope=? AND date BETWEEN ? AND ?', (kind, scope, start, end))
            else:
                db.execute('DELETE FROM observations WHERE kind=? AND scope=?', (kind, scope))
            db.executemany('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',
                           [(kind, scope, r.get('id', 'USD_KRW'), r['date'], json.dumps(r, ensure_ascii=False)) for r in rows])
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',
                       (kind, scope, stamp, json.dumps(raw, ensure_ascii=False)))

    def read(self):
        result = {'apartments': [], 'stocks': [], 'fx': []}
        with self.connect() as db:
            for kind, text in db.execute('SELECT kind,data FROM observations ORDER BY date,item_id'):
                row = json.loads(text)
                for field in ('price', 'price_krw', 'area_m2', 'krw_per_usd'):
                    if field in row:
                        row[field] = Decimal(str(row[field]))
                result[kind].append(row)
        return result

    def stats(self):
        result = {}
        with self.connect() as db:
            for kind in ('apartments', 'stocks', 'fx'):
                n, start, end = db.execute('SELECT COUNT(*),MIN(date),MAX(date) FROM observations WHERE kind=?', (kind,)).fetchone()
                stamp = db.execute('SELECT MAX(fetched_at) FROM snapshots WHERE kind=?', (kind,)).fetchone()[0]
                result[kind] = dict(count=n, start=start, end=end, updated=stamp)
        return result

    def settings(self, value=None):
        with self.connect() as db:
            if value is not None:
                # 키는 SQLite에 쓰지 않음.
                safe = {k: v for k, v in value.items() if k != 'api_key'}
                db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(safe),))
            row = db.execute('SELECT data FROM settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def ranking(self, market=None, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO rankings VALUES(?,?)',(market,json.dumps(value,ensure_ascii=False)))
            rows=db.execute('SELECT market,data FROM rankings').fetchall()
        return {m:json.loads(text) for m,text in rows}

    def rank_cache(self, day):
        with self.connect() as db:
            return {symbol:json.loads(text) for symbol,text in db.execute('SELECT symbol,data FROM rank_cache WHERE date=?',(day,))}

    def cache_rank(self, day, symbol, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO rank_cache VALUES(?,?,?)',(day,symbol,json.dumps(value)))
            db.execute('DELETE FROM rank_cache WHERE date < date(?,\'-14 days\')',(day,))

    def has_scope(self, kind, scope):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM snapshots WHERE kind=? AND scope=?',(kind,scope)).fetchone())

    @staticmethod
    def decode(text):
        row=json.loads(text)
        for field in ('price','price_krw','area_m2','krw_per_usd'):
            if field in row:
                row[field]=Decimal(str(row[field]))
        return row

    def catalog(self):
        result={'apartments':[],'stocks':[]}
        with self.connect() as db:
            for kind in result:
                records=db.execute('''SELECT o.data FROM observations o JOIN
                 (SELECT item_id,MAX(date) AS latest FROM observations WHERE kind=? GROUP BY item_id) x
                 ON o.kind=? AND o.item_id=x.item_id AND o.date=x.latest''',(kind,kind)).fetchall()
                for (text,) in records:
                    row=self.decode(text)
                    result[kind].append({k:v for k,v in row.items() if k in ('id','name','region','area_m2','currency','date')})
        result['ranges']={k:dict(start=v['start'],end=v['end'],count=v['count']) if v['count'] else None for k,v in self.stats().items()}
        result['rankings']=self.ranking()
        return result

    def comparison(self,payload):
        """전체 역사를 메모리에 적재하지 않고 비교에 필요한 관측값만 조회."""
        result={'apartments':[],'stocks':[],'fx':[]}
        with self.connect() as db:
            history=db.execute("SELECT data FROM observations WHERE kind='apartments' AND item_id=? AND date<=? ORDER BY date",(payload.get('apartment'),payload.get('house_date'))).fetchall()
            latest=db.execute('''SELECT o.data FROM observations o JOIN
             (SELECT item_id,MAX(date) AS latest FROM observations WHERE kind='apartments' AND date<=? GROUP BY item_id) x
             ON o.kind='apartments' AND o.item_id=x.item_id AND o.date=x.latest''',(payload.get('house_date'),)).fetchall()
            unique={}
            for (text,) in latest+history:
                r=self.decode(text);unique[(r['id'],r['date'])]=r
            result['apartments']=sorted(unique.values(),key=lambda r:r['date'])
            ids={payload.get('stock')}
            for h in payload.get('holdings',[]):
                ids.add(h.get('id'))
            for item_id in ids:
                row=db.execute("SELECT data FROM observations WHERE kind='stocks' AND item_id=? AND date<=? ORDER BY date DESC LIMIT 1",(item_id,payload.get('stock_date'))).fetchone()
                if row:
                    result['stocks'].append(self.decode(row[0]))
            fxdates=set()
            for stock in result['stocks']:
                if stock['currency']=='USD':
                    row=db.execute("SELECT date,data FROM observations WHERE kind='fx' AND date<=? ORDER BY date DESC LIMIT 1",(stock['date'],)).fetchone()
                    if row and row[0] not in fxdates:
                        fxdates.add(row[0]);result['fx'].append(self.decode(row[1]))
        return result
