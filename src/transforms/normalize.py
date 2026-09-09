"""허가대장·KPX 공통 정규화 규칙.

지침 §10(표준화), §12(SPC), §22(금지사항)를 코드로 옮긴 것이다.
추정이 필요한 값은 채우지 않고 '확인필요'/'자료없음' 상태로 남긴다.
"""

from __future__ import annotations

import re

# ── 발전원 ────────────────────────────────────────────────────────────
# 허가대장 원동력 표기는 띄어쓰기가 불규칙하다: '풍력(해 상)', '신재생(해 상풍력)' 등.
# 공백을 모두 제거한 뒤 판정한다.
OFFSHORE_TOKENS = ("해상풍력", "풍력(해상)", "풍력(해상풍력)", "신재생(해상풍력)")
ONSHORE_TOKENS = ("풍력(육상)", "육상풍력")
SOLAR_TOKENS = ("태양광", "수상태양광", "신재생(수상태양광)")

EXCLUDED_TOKENS = ("연료전지", "바이오", "수력", "화력", "원자력", "매립가스",
                   "부생가스", "부생수소", "수소", "석탄", "lng", "천연가스",
                   "집단에너지", "열병합", "복합", "ess", "bess",
                   "자원회수", "폐자원", "폐기물", "소각", "srf", "고형연료")


def normalize_energy_source(raw: str) -> tuple[str, str]:
    """원동력 원문 -> (발전원_표준, 판정상태).

    반환 발전원_표준: 태양광 / 육상풍력 / 해상풍력 / 풍력_구분미상 / 대상외 / 확인필요
    """
    if not raw:
        return "확인필요", "자료없음"
    text = re.sub(r"\s+", "", raw).lower()

    if any(tok in text for tok in EXCLUDED_TOKENS):
        return "대상외", "확정"
    if any(tok in text for tok in OFFSHORE_TOKENS) or "해상" in text:
        return "해상풍력", "확정"
    if any(tok in text for tok in ONSHORE_TOKENS) or ("육상" in text and "풍력" in text):
        return "육상풍력", "확정"
    if "태양광" in text:
        return "태양광", "확정"
    if "풍력" in text:
        # 허가대장은 다수가 '풍력'으로만 적혀 육상/해상 구분이 없다. 추정하지 않는다.
        return "풍력_구분미상", "확인필요"
    if text in ("", "“", "”", "\"", "-"):
        return "확인필요", "자료없음"
    return "대상외", "잠정"


# ── 날짜 ──────────────────────────────────────────────────────────────
DITTO_MARKS = {"“", "”", "\"", "〃", "″", "same", "상동"}

ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# '07.10.30 / ‘08.3.31 / `01.11.1
SHORT_RE = re.compile(r"^[`'‘’\"]?\s*(\d{2})\s*\.\s*(\d{1,2})\s*(?:\.\s*(\d{1,2}))?\.?$")
FULL_RE = re.compile(r"^(\d{4})\s*\.\s*(\d{1,2})\s*(?:\.\s*(\d{1,2}))?\.?$")


def normalize_date(raw: str, previous: str | None = None) -> tuple[str | None, str]:
    """허가대장 날짜 -> (YYYY-MM-DD | YYYY-MM, 상태).

    되풀이표('“')는 바로 위 행과 같은 값이라는 뜻이므로 previous 를 이어받는다.
    변환할 수 없으면 값을 만들어내지 않고 (None, '확인필요') 를 돌려준다.
    """
    text = (raw or "").strip()
    if not text:
        return None, "자료없음"
    if text in DITTO_MARKS:
        return (previous, "확정_되풀이") if previous else (None, "확인필요")

    m = ISO_RE.match(text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "확정"

    m = SHORT_RE.match(text)
    if m:
        year = 2000 + int(m.group(1))
        if m.group(3):
            return f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "확정"
        return f"{year}-{int(m.group(2)):02d}", "확정_월까지"

    m = FULL_RE.match(text)
    if m:
        if m.group(3):
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "확정"
        return f"{m.group(1)}-{int(m.group(2)):02d}", "확정_월까지"

    return None, "확인필요"


# ── 법인명 ────────────────────────────────────────────────────────────
CORP_PREFIX_RE = re.compile(r"㈜|\(주\)|주식회사|\(유\)|유한회사|㈕")


def normalize_company(raw: str) -> str:
    """검색·중복판정용 표준 법인명. 원문은 별도 보존한다(지침 §10.3)."""
    if not raw:
        return ""
    text = CORP_PREFIX_RE.sub(" ", raw)
    return re.sub(r"\s+", "", text).strip()


# ── 지역 ──────────────────────────────────────────────────────────────
GWANGJU_DISTRICTS = ("동구", "서구", "남구", "북구", "광산구")
JEONNAM_CITIES = ("목포시", "여수시", "순천시", "나주시", "광양시")
JEONNAM_COUNTIES = ("담양군", "곡성군", "구례군", "고흥군", "보성군", "화순군", "장흥군",
                    "강진군", "해남군", "영암군", "무안군", "함평군", "영광군", "장성군",
                    "완도군", "진도군", "신안군")
# 허가대장은 '전남 영광 백수읍'처럼 시/군 접미사를 생략하기도 한다
JEONNAM_SHORT = tuple(name[:-1] for name in JEONNAM_CITIES + JEONNAM_COUNTIES)


def classify_region(location: str) -> tuple[str, str, str]:
    """위치 원문 -> (지역, 시군구, 판정상태).

    2026-07-01 전남·광주 통합으로 광역 명칭만으로는 두 지역을 나눌 수 없다.
    시군구 기준으로 판정한다.
    """
    if not location:
        return "확인필요", "", "자료없음"
    text = re.sub(r"\s+", " ", location)

    # 타 광역시·도가 먼저 나오면 대상 지역이 아니다. '경기도 광주시'가 '광주'에
    # 걸려 광주광역시로 오인되는 것을 막는다(경기 광주시, 강원 등).
    if re.match(r"^\(?(?:당초|변경)?\)?\s*(경기|강원|충청|충북|충남|경상|경북|경남|"
                r"서울|인천|대전|대구|부산|울산|세종|전북|전라북도|제주)", text):
        return "대상외지역", "", "확정"

    # 광주광역시는 자치구(동·서·남·북·광산)가 있어야 확정한다. '광주시'(경기)는 제외.
    is_gwangju_metro = ("광주광역시" in text) or re.search(r"광주\s*[동서남북광]구", text)
    for district in GWANGJU_DISTRICTS:
        if district in text and is_gwangju_metro:
            return "광주", district, "확정"

    for name in JEONNAM_CITIES + JEONNAM_COUNTIES:
        if name in text:
            return "전남", name, "확정"

    if "전남" in text or "전라남도" in text or "전남광주통합특별시" in text:
        for short in JEONNAM_SHORT:
            if re.search(rf"전남\s*{short}\b|전라남도\s*{short}", text):
                full = next(n for n in JEONNAM_CITIES + JEONNAM_COUNTIES
                            if n.startswith(short))
                return "전남", full, "확정_약칭"
        return "전남", "", "확인필요"

    if is_gwangju_metro:
        return "광주", "", "확인필요"
    return "확인필요", "", "확인필요"


# ── 용량 ──────────────────────────────────────────────────────────────
def normalize_capacity(raw: str) -> tuple[float | None, str]:
    """용량 원문 -> (MW 값, 상태). kW 표기는 MW 로 환산하고 원문은 보존한다."""
    if not raw:
        return None, "자료없음"
    text = raw.replace(",", "").strip()

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text), "확정"

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:kw|kW|㎾)", text)
    if m:
        return float(m.group(1)) / 1000, "확정_kW환산"

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:mw|MW|㎿)", text)
    if m:
        return float(m.group(1)), "확정"

    # '45만kw×2', '3.6MW×20기' 등 복합 표기는 확정하지 않는다
    return None, "확인필요"
