"""온라인 자료 수집. 인증키/요청 URL은 오류 메시지에 포함하지 않습니다."""
import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from core import number

MOLIT = 'https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade'
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'
FX = 'https://api.frankfurter.dev/v1/'
REGIONS = {'11110': '서울 종로구', '11200': '서울 성동구', '11680': '서울 강남구',
           '11650': '서울 서초구', '11710': '서울 송파구', '11440': '서울 마포구',
           '28185': '인천 연수구', '41117': '경기 수원시 영통구', '41135': '경기 성남시 분당구'}


class ProviderError(ValueError):
    pass


def fetch(url, params=None, json_response=True):
    full = url + ('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(3):
        try:
            req = urllib.request.Request(full, headers={'User-Agent': 'Mozilla/5.0 PropertySimulator/0.2', 'Accept': 'application/json, application/xml, text/xml'})
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read(30_000_001)
            if len(raw) > 30_000_000:
                raise ProviderError('제공처 응답이 너무 큽니다. 수집 범위를 줄이세요.')
            return json.loads(raw) if json_response else raw
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1 + attempt * 2)
                continue
            raise ProviderError(f'제공처 HTTP {exc.code}: 인증·호출 제한·제공처 상태를 확인하세요. 기존 자료는 보존됩니다.') from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt < 2:
                time.sleep(1 + attempt * 2)
                continue
            raise ProviderError('연결 실패 또는 시간 초과: 인터넷 연결과 제공처 상태를 확인하세요.') from None
        except (json.JSONDecodeError, UnicodeError):
            raise ProviderError('제공처가 예상한 JSON을 반환하지 않았습니다.') from None


def parse_molit(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ProviderError('국토교통부 XML 응답을 읽을 수 없습니다.') from None
    code = root.findtext('.//resultCode')
    if code not in ('00', '000', '0'):
        # 원문 에러에는 인증키가 포함될 가능성이 있으므로 내보내지 않음.
        raise ProviderError('국토교통부 API 오류: 인증키(Decoding)·활용신청 승인·호출 한도를 확인하세요.')
    try:
        total = int(root.findtext('.//totalCount'))
        if total < 0 or total > 100000:
            raise ValueError()
    except (TypeError, ValueError):
        raise ProviderError('국토교통부 응답의 전체 건수가 올바르지 않습니다.') from None
    items = [{node.tag: (node.text or '').strip() for node in item} for item in root.findall('.//items/item')]
    return items, total


def apartment_rows(items, region_code, month):
    groups = defaultdict(list)
    metadata = {}
    cancelled = 0
    for item in items:
        if item.get('cdealType', '').strip().upper() in ('O', 'Y', '1') or item.get('cdealDay', '').strip() not in ('', '-'):
            cancelled += 1
            continue
        try:
            name, town, address = item['aptNm'].strip(), item['umdNm'].strip(), item['jibun'].strip()
            if not name or not town or not address:
                raise ValueError()
            if item.get('sggCd', region_code) != region_code:
                raise ValueError()
            d = date(int(item['dealYear']), int(item['dealMonth']), int(item['dealDay']))
            if d.strftime('%Y%m') != month or d > date.today():
                raise ValueError()
            area = format(number(item['excluUseAr'], '면적').normalize(), 'f')
            price = number(item['dealAmount'].replace(',', '').strip(), '거래가격') * 10000
        except (KeyError, ValueError, TypeError):
            raise ProviderError('국토교통부 거래 필드가 누락되거나 잘못되었습니다. 해당 월은 갱신하지 않았습니다.') from None
        identity = '|'.join((region_code, town, address, name, area))
        item_id = 'APT_' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        key = (item_id, d.isoformat())
        groups[key].append(price)
        metadata[key] = dict(id=item_id, name=f'{name} ({town} {address})', region=REGIONS.get(region_code, f'지역코드 {region_code}'), area_m2=area, date=d.isoformat())
    rows = []
    for key, prices in groups.items():
        rows.append({**metadata[key], 'price_krw': str(median(prices)),
                     'source': f'국토교통부 · 같은 단지/면적/계약일 {len(prices)}건 중위값 · 최저 {min(prices):,.0f} / 최고 {max(prices):,.0f}원'})
    return sorted(rows, key=lambda r: (r['date'], r['id'])), cancelled


def fetch_apartments(key, region_code, month, transport=fetch):
    if not key:
        raise ProviderError('아파트 수집에는 공공데이터포털 인증키가 필요합니다.')
    items, page, expected = [], 1, None
    fingerprints = set()
    while True:
        raw = transport(MOLIT, {'serviceKey': urllib.parse.unquote(key.strip()), 'LAWD_CD': region_code,
                               'DEAL_YMD': month, 'pageNo': page, 'numOfRows': 1000}, json_response=False)
        chunk, total = parse_molit(raw)
        if expected is not None and expected != total:
            raise ProviderError('수집 중 거래 건수가 변경되었습니다. 해당 월을 다시 수집하세요.')
        expected = total
        if chunk:
            digest = hashlib.sha256(json.dumps(chunk, sort_keys=True).encode()).hexdigest()
            if digest in fingerprints:
                raise ProviderError('제공처가 같은 페이지를 반복했습니다. 해당 월은 저장하지 않았습니다.')
            fingerprints.add(digest)
        items.extend(chunk)
        if len(items) >= total:
            break
        if not chunk or page >= 100:
            raise ProviderError('거래 페이지 수집이 완료되지 않아 해당 월을 저장하지 않았습니다.')
        page += 1
        time.sleep(.15)
    if len(items) != expected:
        raise ProviderError('수집 건수가 제공처의 전체 건수와 다릅니다.')
    rows, cancelled = apartment_rows(items, region_code, month)
    return rows, items, cancelled


def parse_yahoo(payload, symbol, start, end, now=None):
    """Yahoo Close의 분할 조정을 역산. 배당 조정 값 Adj Close는 사용하지 않음."""
    now = now or datetime.now(timezone.utc)
    try:
        chart = payload['chart']
        if chart.get('error') or not chart.get('result'):
            raise ProviderError('주가를 찾지 못했습니다. 종목 코드와 상장 기간을 확인하세요.')
        data = chart['result'][0]
        meta = data['meta']
        if meta.get('instrumentType') not in ('EQUITY', 'ETF'):
            raise ProviderError('개별 주식과 ETF만 지원합니다. 지수는 주식 수로 환산하지 않습니다.')
        currency = meta['currency']
        expected = 'KRW' if symbol.endswith(('.KS', '.KQ')) else 'USD'
        if currency != expected:
            raise ProviderError('선택한 시장과 제공처 통화가 일치하지 않습니다.')
        market_tz = timezone(timedelta(seconds=int(meta.get('gmtoffset', 0))))
        market_today = now.astimezone(market_tz).date()
        events = []
        for split in data.get('events', {}).get('splits', {}).values():
            d = datetime.fromtimestamp(int(split['date']), market_tz).date()
            ratio = number(split['numerator'], '분할 비율') / number(split['denominator'], '분할 비율')
            if d <= market_today:
                events.append((d, ratio))
        times = data.get('timestamp') or []
        closes = data['indicators']['quote'][0].get('close') or []
        volumes = data['indicators']['quote'][0].get('volume') or []
        if len(times) != len(closes):
            raise ProviderError('주가 날짜와 가격 개수가 다릅니다.')
        rows = []
        for index, (timestamp, close) in enumerate(zip(times, closes)):
            d = datetime.fromtimestamp(timestamp, market_tz).date()
            # 오늘 데이터는 장중 가격일 수 있어 제외. 현지 전일까지 사용.
            if close is None or (index < len(volumes) and volumes[index] == 0) or not start <= d <= end or d >= market_today:
                continue
            factor = Decimal(1)
            for split_date, ratio in events:
                if split_date > d:
                    factor *= ratio
            reconstructed = number(close, '종가') * factor
            rows.append({'id': symbol, 'name': meta.get('longName') or meta.get('shortName') or symbol,
                         'currency': currency, 'date': d.isoformat(), 'price': str(reconstructed),
                         'source': f'Yahoo Finance · 분할역산 추정 종가 · 배당 미조정 · 역산배수 {factor}'})
        if not rows:
            raise ProviderError('선택 기간에 완료된 일별 주가가 없습니다. 상장일·기간을 확인하세요.')
        return sorted(rows, key=lambda r: r['date'])
    except ProviderError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        raise ProviderError('주가 응답 형식 또는 분할 정보가 올바르지 않습니다.') from None


def fetch_stock(symbol, start, end, transport=fetch):
    if not re.fullmatch(r'(?:\d{6}\.(?:KS|KQ)|[A-Z][A-Z0-9.-]{0,14})', symbol):
        raise ProviderError('종목 코드는 005930.KS, 035900.KQ, AAPL 등의 형식이어야 합니다.')
    # 요청 종료 이후의 분할도 알아야 과거 실제 주식 단위를 역산할 수 있음.
    params = {'period1': int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp()),
              'period2': int(datetime.now(timezone.utc).timestamp()), 'interval': '1d', 'events': 'splits'}
    payload = transport(YAHOO + urllib.parse.quote(symbol, safe=''), params)
    return parse_yahoo(payload, symbol, start, end), payload


def parse_fx(payload, start, end):
    try:
        if payload.get('base') != 'USD' or not isinstance(payload.get('rates'), dict):
            raise ValueError()
        rows = []
        for d, values in payload['rates'].items():
            actual = date.fromisoformat(d)
            if start <= actual <= end:
                rows.append({'date': d, 'krw_per_usd': str(number(values['KRW'], '환율')),
                             'source': 'Frankfurter v1 · ECB 기준환율 · USD/KRW 교차환율'})
        # 주말/휴일만 포함한 짧은 범위에서는 0행도 정상.
        return sorted(rows, key=lambda r: r['date'])
    except (KeyError, TypeError, ValueError):
        raise ProviderError('환율 응답의 날짜·통화·숫자가 올바르지 않습니다.') from None


def fetch_fx(start, end, transport=fetch):
    payload = transport(FX + f'{start.isoformat()}..{end.isoformat()}', {'base': 'USD', 'symbols': 'KRW'})
    return parse_fx(payload, start, end), payload
