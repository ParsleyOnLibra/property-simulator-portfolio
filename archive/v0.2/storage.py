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
