"""VWorld 지오코더로 육상 발전소 좌표를 얻는다 (지침 §15).

우선순위:
  1) 지번주소 지오코딩 (PARCEL)         → 좌표정확도 '정확'
  2) 실패 시 도로명주소 (ROAD)          → '정확'
  3) 실패 시 읍면동 대표좌표 (지번 PARCEL) → '추정', 좌표유형 '읍면동대표'
어느 것도 안 되면 '좌표없음'. 임의 좌표를 만들지 않는다.

API 키는 .env(VWORLD_API_KEY)에서 읽는다. 코드·로그·산출물에 키를 남기지 않는다.
응답 좌표가 전남·광주 범위를 벗어나면 채택하지 않고 검수 대상으로 표시한다.

허가위치 원문에서 비교/조회용 주소를 뽑을 때 '일원', '등 OO필지', '외 O필지',
'(당초)' 같은 표현은 조회 문자열에서만 제거하고 원문은 보존한다(지침 §10.4).
"""

from __future__ import annotations

import os
import re
import time

import requests

from .coordinates import in_range

VWORLD_URL = "https://api.vworld.kr/req/address"

NOISE_PATTERNS = [
    r"\(당초\)", r"\(변경\)", r"일원", r"인근", r"공유수면", r"해상", r"앞바다",
    r"등\s*\d+\s*필지", r"외\s*\d+\s*필지", r"등\s*\d+\s*개?\s*필지",
    r"외\s*\d+\s*개?\s*필지", r"일대", r"지선", r"부지\s*내",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS))
# 여러 지번이 쉼표로 나열되면 첫 지번만 조회에 쓴다
MULTI_JIBUN_RE = re.compile(r",|·|/")
EUP_MYEON_DONG_RE = re.compile(r"(.+?[시군구]\s*.+?(?:읍|면|동))")


# 관찰된 OCR/오탈자 교정 (조회 문자열에만 적용, 원문은 보존)
OCR_TYPO_FIXES = {"지도옵": "지도읍", "지도 옵": "지도읍"}


def clean_query_address(raw: str) -> str:
    """조회용 주소. 첫 지번만 남기고 잡표현·명백한 오타를 제거한다."""
    text = str(raw or "").strip()
    text = re.sub(r"^\((?:당초|변경)\)\s*", "", text)
    for wrong, right in OCR_TYPO_FIXES.items():
        text = text.replace(wrong, right)
    text = MULTI_JIBUN_RE.split(text)[0]
    text = NOISE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def eup_myeon_dong(raw: str) -> str:
    """읍면동 단위까지만 남긴 대표 주소."""
    m = EUP_MYEON_DONG_RE.search(str(raw or ""))
    return m.group(1).strip() if m else ""


class VWorldGeocoder:
    def __init__(self, api_key: str | None = None, delay_sec: float = 0.3,
                 timeout_sec: int = 20):
        self.api_key = api_key or os.getenv("VWORLD_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("VWORLD_API_KEY 가 설정되지 않았습니다 (.env 확인).")
        self.delay_sec = delay_sec
        self.timeout_sec = timeout_sec
        self.session = requests.Session()

    def _request(self, address: str, addr_type: str) -> tuple[float, float] | None:
        params = {"service": "address", "request": "getcoord", "version": "2.0",
                  "crs": "epsg:4326", "format": "json", "type": addr_type,
                  "address": address, "key": self.api_key}
        try:
            r = self.session.get(VWORLD_URL, params=params, timeout=self.timeout_sec)
            data = r.json()
        except Exception:
            return None
        finally:
            time.sleep(self.delay_sec)
        if data.get("response", {}).get("status") != "OK":
            return None
        point = data["response"]["result"]["point"]
        return float(point["y"]), float(point["x"])      # (lat, lon)

    def geocode(self, raw_address: str) -> dict:
        """단계별로 좌표를 시도하고 정확도 등급을 붙여 돌려준다."""
        result = {"위도": None, "경도": None, "좌표유형": "좌표없음",
                  "좌표정확도": "좌표없음", "판정근거": "", "조회주소": ""}

        query = clean_query_address(raw_address)
        if not query:
            result["판정근거"] = "조회 가능한 주소 없음"
            return result

        for addr_type, accuracy, label in (("PARCEL", "정확", "지번"),
                                           ("ROAD", "정확", "도로명")):
            hit = self._request(query, addr_type)
            if hit and in_range(*hit):
                result.update(위도=hit[0], 경도=hit[1], 좌표유형=label,
                              좌표정확도=accuracy, 조회주소=query,
                              판정근거=f"VWorld {label} 지오코딩")
                return result

        emd = eup_myeon_dong(raw_address)
        if emd:
            hit = self._request(emd, "PARCEL")
            if hit and in_range(*hit):
                result.update(위도=hit[0], 경도=hit[1], 좌표유형="읍면동대표",
                              좌표정확도="추정", 조회주소=emd,
                              판정근거=f"읍면동 대표좌표 ({emd})")
                return result

        result["판정근거"] = f"지오코딩 실패 (조회주소: {query})"
        result["조회주소"] = query
        return result
