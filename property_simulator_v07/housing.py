"""지정 단지 필터. 동명이 단지를 막기 위해 구·법정동·이름을 함께 확인."""
import re

TARGETS = [
    {'name':'한남더힐', 'region':'11170', 'town':'한남동', 'pattern':r'한남더힐', 'type':'apartment'},
    {'name':'트리마제', 'region':'11200', 'town':'성수동1가', 'pattern':r'트리마제', 'type':'apartment'},
    {'name':'압구정 현대아파트', 'region':'11680', 'town':'압구정동', 'pattern':r'(?:현대|현대아파트|현대[0-9]+차(?:아파트)?(?:\([^)]*\))?)', 'type':'apartment'},
    {'name':'은마아파트', 'region':'11680', 'town':'대치동', 'pattern':r'은마(?:아파트)?', 'type':'apartment'},
    {'name':'나인원 한남', 'region':'11170', 'town':'한남동', 'pattern':r'나인원한남', 'type':'apartment'},
    {'name':'래미안 원베일리', 'region':'11650', 'town':'반포동', 'pattern':r'래미안원베일리', 'type':'apartment'},
    {'name':'타워팰리스', 'region':'11680', 'town':'도곡동', 'pattern':r'타워팰리스(?:[123ⅠⅡⅢ]|[123]차)?', 'type':'apartment'},
    {'name':'시그니엘 (롯데월드타워)', 'region':'11710', 'town':'신천동', 'pattern':r'롯데월드타워(?:앤드롯데월드몰)?', 'type':'officetel'},
]
APARTMENT_REGIONS = tuple(dict.fromkeys(t['region'] for t in TARGETS if t['type']=='apartment'))
REGION_CODES = {'서울 용산구':'11170','서울 성동구':'11200','서울 강남구':'11680','서울 서초구':'11650','서울 송파구':'11710'}


def normalize(value):
    return re.sub(r'\s+', '', value)


def match_target(region, town, name, property_type='apartment'):
    for target in TARGETS:
        if (region == target['region'] and normalize(town) == target['town']
                and property_type == target['type']
                and re.fullmatch(target['pattern'], normalize(name))):
            return target
    return None


def selected_row(row):
    # v0.4 저장 데이터는 이름 끝의 '(법정동 지번)'에서 주소를 복원합니다.
    name = row.get('building_name')
    town = row.get('town')
    if not name or not town:
        matched = re.fullmatch(r'(.*) \((\S+) [^()]+\)', row.get('name',''))
        if not matched:
            return False
        name, town = matched.groups()
    region = row.get('region_code') or REGION_CODES.get(row.get('region'))
    kind = row.get('property_type') or ('officetel' if row.get('id','').startswith('OFFI_') else 'apartment')
    return bool(match_target(region, town, name, kind))
