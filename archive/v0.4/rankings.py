"""같은 거래일의 종가 × 거래량 순위. 장중 값과 거래량 순위로 대체하지 않음."""
import csv
import io
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from providers import fetch, ProviderError, number, YAHOO, completed_bar

SEOUL = dict(zip(
    ['11110','11140','11170','11200','11215','11230','11260','11290','11305','11320','11350','11380','11410','11440','11470','11500','11530','11545','11560','11590','11620','11650','11680','11710','11740'],
    ['종로구','중구','용산구','성동구','광진구','동대문구','중랑구','성북구','강북구','도봉구','노원구','은평구','서대문구','마포구','양천구','강서구','구로구','금천구','영등포구','동작구','관악구','서초구','강남구','송파구','강동구']))
KR_URL = 'https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo'
NASDAQ_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/'


class Cancelled(Exception):
    pass


def top20(rows):
    ranked = sorted(rows, key=lambda r: (-Decimal(str(r['close'])) * Decimal(str(r['volume'])), r['symbol']))[:20]
    return [{**r, 'rank': i+1, 'amount': str(Decimal(str(r['close'])) * Decimal(str(r['volume'])))} for i,r in enumerate(ranked)]


def kr_page(payload, target):
    try:
        response = payload['response']
        if str(response['header']['resultCode']) not in ('0','00','000'):
            raise ProviderError('국내 순위 API 권한 오류: 금융위원회_주식시세정보 활용신청 승인과 인증키를 확인하세요.')
        body = response['body']
        total = int(body['totalCount'])
        if not 0 <= total <= 50000:
            raise ValueError()
        items = (body.get('items') or {}).get('item', [])
        if isinstance(items,dict):
            items = [items]
        rows = []
        for item in items:
            if item['basDt'] != target:
                raise ProviderError('국내 순위 응답에 서로 다른 거래일이 포함되어 있습니다.')
            if item['mrktCtg'] not in ('KOSPI','KOSDAQ'):
                continue
            code = item['srtnCd'].removeprefix('A')
            if not re.fullmatch(r'\d{6}',code):
                raise ValueError()
            close = number(item['clpr'],'종가',True)
            volume = number(item['trqu'],'거래량',True)
            if close and volume:
                rows.append(dict(symbol=code+('.KS' if item['mrktCtg']=='KOSPI' else '.KQ'),name=item['itmsNm'],
                                 date=date.fromisoformat(f'{target[:4]}-{target[4:6]}-{target[6:]}').isoformat(),
                                 close=str(close),volume=str(volume),currency='KRW'))
        return rows,total,len(items)
    except ProviderError:
        raise
    except (KeyError,TypeError,ValueError):
        raise ProviderError('국내 주식시세 API의 응답 형식이 올바르지 않습니다.') from None


def collect_kr(key, progress, stopped, transport=fetch):
    if not key:
        raise ProviderError('국내 순위에는 금융위원회_주식시세정보를 승인받은 공공데이터포털 키가 필요합니다.')
    today = datetime.now(timezone(timedelta(hours=9))).date()
    for offset in range(1,16):
        target = (today-timedelta(days=offset)).strftime('%Y%m%d')
        if stopped():
            raise Cancelled()
        rows, page, count, expected, seen = [],1,0,None,set()
        while True:
            progress(f'국내 순위 · {target} {page}페이지 확인 중')
            params=dict(serviceKey=urllib.parse.unquote(key),basDt=target,pageNo=page,numOfRows=1000,resultType='json')
            chunk,total,received=kr_page(transport(KR_URL,params),target)
            if expected is not None and total != expected:
                raise ProviderError('국내 순위 전체 건수가 수집 중 바뀌었습니다. 재시도하세요.')
            expected=total
            for row in chunk:
                if row['symbol'] in seen:
                    raise ProviderError('국내 순위 페이지에 중복 종목이 있습니다. 순위를 갱신하지 않습니다.')
                seen.add(row['symbol'])
            rows.extend(chunk);count+=received
            if count==total:
                break
            if received==0 or count>total or page>=50:
                raise ProviderError('국내 전 종목 페이지를 모두 받지 못했습니다.')
            page+=1
            if stopped():
                raise Cancelled()
            time.sleep(.2)
        if expected:
            if len(rows)<20:
                raise ProviderError('국내 거래 종목이 20개 미만이어서 순위를 갱신하지 않습니다.')
            return dict(market='KR',date=rows[0]['date'],status='complete',universe=expected,scanned=expected,
                        eligible=len(rows),missing=[],top=top20(rows),source='금융위원회 주식시세 · KOSPI+KOSDAQ · ETF/ETN 제외 · 우선주 포함',
                        definition='종가 × 당일 거래량 · 추정 거래대금')
    raise ProviderError('최근 15일 중 국내 종가 자료를 확보하지 못했습니다. 제공 지연·휴장·권한을 확인하세요.')


def us_universe(nasdaq_text,other_text):
    """ETF·시험종목·우선주·채권·권리·워런트·유닛을 제외한 보통주/ADR 범위."""
    result={}
    excluded=re.compile(r'\b(warrants?|rights?|units?|preferred|preference|notes?|debentures?|bonds?)\b|\bSeries [A-Z].*Preferred',re.I)
    for text,is_nasdaq in ((nasdaq_text,True),(other_text,False)):
        reader=csv.DictReader(io.StringIO(text),delimiter='|')
        fields=reader.fieldnames or []
        if 'Security Name' not in fields or 'ETF' not in fields or 'Test Issue' not in fields:
            raise ProviderError('미국 상장 종목 목록 형식이 바뀌었습니다.')
        for item in reader:
            symbol=(item.get('Symbol') if is_nasdaq else item.get('ACT Symbol')) or ''
            if symbol.startswith('File Creation Time'):
                continue
            name=item.get('Security Name') or ''
            if item.get('ETF')!='N' or item.get('Test Issue')!='N' or excluded.search(name):
                continue
            if not is_nasdaq and item.get('Exchange') not in ('N','A'):
                continue
            yahoo_symbol=symbol.replace('.','-')
            if not re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}',yahoo_symbol):
                continue
            if yahoo_symbol in result and result[yahoo_symbol]['listing_symbol']!=symbol:
                raise ProviderError('미국 종목 코드 변환 충돌이 있습니다.')
            result[yahoo_symbol]=dict(symbol=yahoo_symbol,name=name,listing_symbol=symbol)
    if not result:
        raise ProviderError('미국 주식 목록이 비어 있습니다.')
    return sorted(result.values(),key=lambda r:r['symbol'])


def daily_us(payload,target=None,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        chart=payload['chart']
        if chart.get('error') or not chart.get('result'):
            raise ProviderError('일별 주가 응답 없음')
        data=chart['result'][0];meta=data['meta']
        if meta['currency']!='USD' or meta.get('instrumentType')!='EQUITY':
            return None
        tz=timezone(timedelta(seconds=int(meta.get('gmtoffset',-18000))))
        today=now.astimezone(tz).date()
        q=data['indicators']['quote'][0]
        stamps=data.get('timestamp') or [];closes=q.get('close') or [];volumes=q.get('volume') or []
        if not len(stamps)==len(closes)==len(volumes):
            raise ValueError()
        rows=[]
        for stamp,close,vol in zip(stamps,closes,volumes):
            day=datetime.fromtimestamp(stamp,tz).date().isoformat()
            if not completed_bar(date.fromisoformat(day),meta,now,tz) or (target and day!=target) or close is None or vol is None:
                continue
            c,v=number(close,'종가',True),number(vol,'거래량',True)
            if c and v:
                rows.append(dict(symbol=meta['symbol'],name=meta.get('longName') or meta.get('shortName') or meta['symbol'],
                                 date=day,close=str(c),volume=str(v),currency='USD'))
        return max(rows,key=lambda r:r['date']) if rows else None
    except ProviderError:
        raise
    except (KeyError,TypeError,ValueError,IndexError):
        raise ProviderError('미국 일별 주가 응답 형식 오류') from None


def collect_us(store,progress,stopped,transport=fetch,refresh=False):
    raw1=transport(NASDAQ_URL+'nasdaqlisted.txt',json_response=False).decode('utf-8-sig')
    raw2=transport(NASDAQ_URL+'otherlisted.txt',json_response=False).decode('utf-8-sig')
    universe=us_universe(raw1,raw2)
    params=dict(range='1mo',interval='1d')
    # 두 기준 종목이 같은 완료 거래일을 반환하는지 확인.
    anchor=[daily_us(transport(YAHOO+symbol,params)) for symbol in ('AAPL','MSFT')]
    if not all(anchor) or anchor[0]['date']!=anchor[1]['date']:
        raise ProviderError('미국 완료 거래일을 확정하지 못했습니다. 기존 순위를 유지합니다.')
    target=anchor[0]['date']
    cached={} if refresh else store.rank_cache(target)
    rows=[];missing=[];no_trade=[];failed=0
    pending_items=[]
    for item in universe:
        if item['symbol'] in cached:
            rows.append(cached[item['symbol']])
        else:
            pending_items.append(item)
    completed=len(rows)
    def download(item):
        time.sleep(.2)
        row=daily_us(transport(YAHOO+urllib.parse.quote(item['symbol'],safe=''),params),target)
        if row:
            row['symbol']=item['symbol']
        return row
    pool=ThreadPoolExecutor(max_workers=3)
    pending={};cursor=iter(pending_items)
    try:
        while True:
            if stopped():
                raise Cancelled()
            while len(pending)<3:
                item=next(cursor,None)
                if item is None:
                    break
                pending[pool.submit(download,item)]=item['symbol']
            if not pending:
                break
            done,_=wait(pending,timeout=.5,return_when=FIRST_COMPLETED)
            for future in done:
                symbol=pending.pop(future);completed+=1
                try:
                    row=future.result();failed=0
                    if row:
                        rows.append(row);store.cache_rank(target,symbol,row)
                    else:
                        no_trade.append(symbol)
                except ProviderError:
                    failed+=1;missing.append(symbol)
                    if failed>=3:
                        raise ProviderError('미국 종목 조회 연속 3회 실패. 이전 순위와 종목별 캐시는 유지됩니다. 잠시 후 재수집하세요.') from None
                progress(f'미국 전 종목 확인 · {completed:,}/{len(universe):,} · {symbol} · {target}')
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=False,cancel_futures=True)
    if len(rows)<20:
        raise ProviderError('미국 거래 자료가 20종목 미만입니다.')
    # 무거래인지 누락인지 단정할 수 없으므로 둘 다 범위 제한 표시.
    unresolved=missing+no_trade
    return dict(market='US',date=target,status='partial' if unresolved else 'complete',universe=len(universe),
                scanned=len(universe),eligible=len(rows),missing=unresolved,top=top20(rows),
                source='Nasdaq Trader 상장목록 + Yahoo 일별 종가 · NASDAQ/NYSE/NYSE American 보통주·ADR 범위',
                definition='종가 × 당일 거래량 · 추정 거래대금',
                note='미확보에는 당일 무거래·신규상장·미지원·오류가 포함될 수 있습니다. 누락이 있으면 확보한 종목 안의 순위입니다.')
