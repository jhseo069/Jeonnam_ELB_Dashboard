"""좌표 추출·변환 (지침 §15).

해상풍력: 허가대장 위치 원문에 사업구역 폴리곤이 위경도 DMS 로 들어 있다.
  '위도 34°53′25.90″ / 경도 125°54′32.57″' 또는 '35°22′09.00″N 125°58′40.10″E'
  이를 십진 WGS84 로 변환하고, 여러 점이면 대표점(중심)과 폴리곤을 함께 보관한다.
  육상 행정주소를 발전기 위치로 쓰지 않는다.

육상(태양광·육상풍력): 좌표 원문이 없으면 VWorld 지번 지오코딩으로 얻는다.
  이 모듈은 원문 좌표 파싱만 담당하고, 지오코딩은 vworld_geocode.py 가 맡는다.

좌표 정확도 등급:
  정확     사업구역 폴리곤 또는 지번 지오코딩 성공
  대표좌표  여러 필지/폴리곤의 중심점
  추정     읍면동 대표좌표
  좌표없음  근거 없음 (임의로 바다에 찍지 않는다)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 34°53′25.90″  /  34°53'25.90"  /  35°22′09.00″N
# 도-분, 분-초 사이에 OCR 공백/줄바꿈이 끼고(126°13 ′57.00″), 분 기호가
# 여닫는 따옴표(′ ' ‘ ’ ´ `) 어느 것이든 오는 경우를 모두 허용한다.
_MIN = r"[′'‘’´`]"
_SEC = r"[″\"“”]"
DMS_RE = re.compile(
    rf"(\d{{1,3}})\s*[°]\s*(\d{{1,2}})\s*{_MIN}?\s*(\d{{1,2}}(?:\s*\.\s*\d+)?)\s*{_SEC}?\s*([NSEW])?")
# 십진수 쌍이 공백 없이 붙은 경우: 33.989167127.179396  (위도.6자리+경도)
DECIMAL_PAIR_RE = re.compile(r"(3[3-5]\.\d{4,6})\s*(12[567]\.\d{4,6})")
# '경도' 뒤에 오면 경도, '위도' 뒤에 오면 위도
LAT_HINT = re.compile(r"위도|lat", re.I)
LON_HINT = re.compile(r"경도|lon", re.I)

# 전남·광주 및 인근 해역의 상식 범위 (지침 §17: 좌표가 범위 안에 있는지 확인)
LAT_RANGE = (33.8, 35.7)
LON_RANGE = (125.0, 127.9)


@dataclass
class CoordinateResult:
    위도: float | None = None
    경도: float | None = None
    좌표유형: str = "좌표없음"      # 사업구역폴리곤 / 대표좌표 / 지번 / 읍면동대표 / 좌표없음
    좌표정확도: str = "좌표없음"    # 정확 / 대표좌표 / 추정 / 좌표없음
    폴리곤: list = field(default_factory=list)   # [(lat, lon), ...]
    원본좌표계: str = "WGS84(위경도 DMS)"
    판정근거: str = ""
    범위이탈: bool = False


def dms_to_decimal(degrees: str, minutes: str, seconds: str, hemi: str | None) -> float:
    value = int(degrees) + int(minutes) / 60 + float(seconds) / 3600
    if hemi in ("S", "W"):
        value = -value
    return round(value, 6)


def parse_polygon(text: str) -> list[tuple[float, float]]:
    """위치 원문에서 (위도, 경도) 점들을 순서대로 뽑는다.

    '위도'/'경도' 힌트가 있으면 그에 맞춰 짝짓고, 없으면 N/E 반구 문자나
    값 범위(위도 33~35, 경도 125~127)로 위·경도를 판별한다.
    """
    # 붙어버린 십진수 쌍(33.98...127.17...)을 먼저 회수한다
    decimal_points: list[tuple[float, float]] = []
    for m in DECIMAL_PAIR_RE.finditer(text):
        lat, lon = float(m.group(1)), float(m.group(2))
        decimal_points.append((round(lat, 6), round(lon, 6)))
    if decimal_points:
        return decimal_points

    tokens = []
    for m in DMS_RE.finditer(text):
        deg, minute, sec, hemi = m.groups()
        dec = dms_to_decimal(deg, minute, sec.replace(" ", ""), hemi)
        tokens.append((m.start(), dec, hemi, int(deg)))

    points: list[tuple[float, float]] = []
    pending_lat = pending_lon = None
    for pos, dec, hemi, deg in tokens:
        # 반구 문자 우선
        if hemi in ("N", "S"):
            pending_lat = dec
        elif hemi in ("E", "W"):
            pending_lon = dec
        else:
            # 힌트 문자로 판별 (좌표 앞 12자 안에 위도/경도 표기)
            head = text[max(0, pos - 12):pos]
            if LAT_HINT.search(head):
                pending_lat = dec
            elif LON_HINT.search(head):
                pending_lon = dec
            elif deg < 40:            # 도 값으로 위/경도 추정 (위도 30대, 경도 120대)
                pending_lat = dec
            else:
                pending_lon = dec
        if pending_lat is not None and pending_lon is not None:
            points.append((pending_lat, pending_lon))
            pending_lat = pending_lon = None
    return points


def in_range(lat: float, lon: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def resolve_offshore(coordinate_text: str) -> CoordinateResult:
    """해상풍력 사업구역 좌표 원문을 대표점 + 폴리곤으로 변환한다."""
    result = CoordinateResult()
    if not coordinate_text or not str(coordinate_text).strip():
        result.판정근거 = "허가대장에 사업구역 좌표 없음"
        return result

    points = parse_polygon(str(coordinate_text))
    valid = [(lat, lon) for lat, lon in points if in_range(lat, lon)]
    if not valid:
        result.판정근거 = f"좌표 파싱 실패 또는 범위 이탈 (원문 점 {len(points)}개)"
        result.범위이탈 = bool(points)
        return result

    result.폴리곤 = valid
    result.위도 = round(sum(p[0] for p in valid) / len(valid), 6)
    result.경도 = round(sum(p[1] for p in valid) / len(valid), 6)
    if len(valid) == 1:
        result.좌표유형, result.좌표정확도 = "사업구역점", "정확"
        result.판정근거 = "허가대장 사업구역 좌표 1점"
    else:
        result.좌표유형, result.좌표정확도 = "사업구역폴리곤", "대표좌표"
        result.판정근거 = f"허가대장 사업구역 폴리곤 {len(valid)}점의 중심"
    return result
