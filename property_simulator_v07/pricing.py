"""사용자가 입력한 단가로 실거래 집값 환산. 날짜의 실제 주가로 가장하지 않음."""
from decimal import Decimal
from core import latest, number, metrics


def compare_price(payload, data, stock):
    if not stock:
        raise ValueError('등록된 종목을 선택하세요.')
    house=latest(data['apartments'],payload.get('house_date'),payload.get('apartment'))
    price=number(payload.get('input_price'),'입력 주가')
    currency=stock['currency']
    rate=number(payload.get('input_fx'),'원/달러 환율') if currency=='USD' else Decimal(1)
    return {**metrics(house['price_krw'],price*rate,0),
            'house':house,'stock':{**stock,'price':price,'date':'사용자 입력','source':'사용자가 지정한 비교 단가 · 특정 날짜의 실제 종가로 확정하지 않음'},
            'fx':dict(krw_per_usd=rate,date='입력 환율',source='화면의 적용 환율 값') if currency=='USD' else None,
            'price_krw':price*rate,'price_input':True,
            'warnings':['입력 가격은 가정한 비교 단가입니다. 그래프의 가까운 종가 날짜는 참고이며 계산 기준일이 아닙니다.']}
