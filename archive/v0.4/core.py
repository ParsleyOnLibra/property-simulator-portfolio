"""가격 환산과 CSV 검증. 외부 통신이나 웹 화면에 의존하지 않습니다."""
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING


def number(value, label, zero=False):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}: 숫자를 입력하세요.') from None
    if not n.is_finite() or n > Decimal('1e18') or (n < 0 if zero else n <= 0):
        raise ValueError(f'{label}: 유효한 {"0 이상" if zero else "양수"} 값을 입력하세요. (최대 10^18)')
    return n


def day(value):
    try:
        d = date.fromisoformat(str(value))
        if d < date(2000, 1, 1) or d > date.today():
            raise ValueError()
        return d
    except ValueError:
        raise ValueError('날짜는 2000-01-01부터 오늘까지 YYYY-MM-DD 형식이어야 합니다.') from None


SCHEMAS = {
    'apartments': ['id', 'name', 'region', 'area_m2', 'date', 'price_krw', 'source'],
    'stocks': ['id', 'name', 'currency', 'date', 'price', 'source'],
    'fx': ['date', 'krw_per_usd', 'source'],
}


def parse_csv(text, kind):
    if not isinstance(text, str):
        raise ValueError('CSV 파일 내용이 필요합니다.')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    fields = SCHEMAS[kind]
    if reader.fieldnames != fields:
        raise ValueError(f'{kind} CSV의 열 순서: {",".join(fields)}')
    rows, seen, metadata = [], set(), {}
    for i, row in enumerate(reader, 2):
        if i > 50002:
            raise ValueError('파일당 최대 50,000행을 지원합니다.')
        if None in row or any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError(f'{kind} {i}행: 빈 값 또는 잘못된 열 수입니다.')
        row = {k: v.strip() for k, v in row.items()}
        row['date'] = day(row['date']).isoformat()
        key = (row.get('id'), row['date'])
        if key in seen:
            raise ValueError(f'{kind} {i}행: 같은 ID·날짜가 중복되었습니다. 대표 거래 1건만 남겨 주세요.')
        seen.add(key)
        for col in ('area_m2', 'price_krw', 'price', 'krw_per_usd'):
            if col in row:
                row[col] = number(row[col], f'{kind} {i}행 {col}')
        if kind == 'stocks' and row['currency'] not in ('KRW', 'USD'):
            raise ValueError('주식 통화는 KRW 또는 USD만 가능합니다.')
        if kind != 'fx':
            identity = tuple(row[k] for k in (('name', 'region', 'area_m2') if kind == 'apartments' else ('name', 'currency')))
            if row['id'] in metadata and metadata[row['id']] != identity:
                raise ValueError(f'{row["id"]}: 이름·면적·지역 또는 통화가 일관되지 않습니다. 면적별로 ID를 분리하세요.')
            metadata[row['id']] = identity
        rows.append(row)
    if kind != 'fx' and not rows:
        raise ValueError(f'{kind}: 데이터가 최소 1행 필요합니다.')
    return sorted(rows, key=lambda x: x['date'])


def dataset(raw):
    if not isinstance(raw, dict):
        raise ValueError('데이터가 필요합니다.')
    return {k: parse_csv(raw.get(k), k) for k in SCHEMAS}


def latest(rows, target, item_id=None):
    target = day(target).isoformat()
    found = [r for r in rows if r['date'] <= target and (item_id is None or r.get('id') == item_id)]
    if not found:
        raise ValueError(f'{item_id or "환율"}: {target} 이전 자료가 없습니다. 미래 자료로 대체하지 않습니다.')
    return max(found, key=lambda x: x['date'])


def quote(data, item_id, target):
    stock = latest(data['stocks'], target, item_id)
    fx = latest(data['fx'], stock['date']) if stock['currency'] == 'USD' else None
    rate = fx['krw_per_usd'] if fx else Decimal(1)
    return {'stock': stock, 'fx': fx, 'price_krw': stock['price'] * rate}


def metrics(house, price, quantity):
    house, price, quantity = number(house, '집값'), number(price, '원화 주가'), number(quantity, '수량', True)
    shares = house / price
    return {'shares': shares, 'whole_shares': int(shares.to_integral_value(rounding=ROUND_CEILING)),
            'value': price * quantity, 'coverage': price * quantity / house * 100,
            'shortfall': max(Decimal(0), house - price * quantity)}


def calculate(payload, stored=None):
    if payload.get('mode') == 'manual':
        currency = payload.get('currency')
        if currency not in ('KRW', 'USD'):
            raise ValueError('통화를 확인하세요.')
        price = number(payload.get('price'), '주가')
        rate = number(payload.get('fx'), '환율') if currency == 'USD' else Decimal(1)
        day(payload.get('house_date'))
        day(payload.get('stock_date'))
        return {'manual': True, 'price_krw': price * rate,
                **metrics(payload.get('house_price'), price * rate, payload.get('quantity'))}
    data = stored if stored is not None else dataset(payload.get('data'))
    house_date, stock_date = payload.get('house_date'), payload.get('stock_date')
    house = latest(data['apartments'], house_date, payload.get('apartment'))
    q = quote(data, payload.get('stock'), stock_date)
    result = metrics(house['price_krw'], q['price_krw'], payload.get('quantity'))
    history = [{'date': r['date'], 'shares': r['price_krw'] / q['price_krw']}
               for r in data['apartments'] if r['id'] == house['id'] and r['date'] <= house_date]
    holdings = payload.get('holdings', [])
    if not isinstance(holdings, list) or len(holdings) > 100:
        raise ValueError('보유 종목은 최대 100개입니다.')
    portfolio, total = [], Decimal(0)
    for holding in holdings:
        qty = number(holding.get('quantity'), '보유 수량', True)
        if not qty:
            continue
        hq = quote(data, holding.get('id'), stock_date)
        value = hq['price_krw'] * qty
        portfolio.append({**hq, 'quantity': qty, 'value': value})
        total += value
    affordable = []
    latest_houses = {}
    for row in data['apartments']:
        if row['date'] <= house_date and (row['id'] not in latest_houses or row['date'] > latest_houses[row['id']]['date']):
            latest_houses[row['id']] = row
    affordable = [row for row in latest_houses.values() if row['price_krw'] <= total]
    warnings = []
    observations = [('아파트', house['date'], house_date), ('주가', q['stock']['date'], stock_date)]
    if q['fx']:
        observations.append(('환율', q['fx']['date'], q['stock']['date']))
    for label, actual, requested in observations:
        age = (day(requested) - day(actual)).days
        if age:
            warnings.append(f'{label}: 기준일보다 {age:,}일 이전 자료를 사용했습니다.')
    return {**result, **q, 'house': house, 'history': history, 'warnings': warnings,
            'portfolio': portfolio, 'portfolio_total': total,
            'portfolio_coverage': total / house['price_krw'] * 100,
            'affordable_count': len(affordable), 'affordable': sorted(affordable, key=lambda r: r['price_krw'])[:200]}
