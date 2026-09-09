"""풍력_구분미상 최종 재분류 (좌표·공유수면 근거).

허가대장 편입분은 원동력이 '풍력'으로만 적혀 육상/해상 미상으로 남는다. 이를
위치 원문과 허가대장 좌표원문의 실제 근거로 최종 판정한다. 추정이 아니라
원문에 기재된 표지(공유수면·해상·위경도 좌표·육상 지번)에만 근거한다(지침 §22).

  해상풍력 : 위치/좌표원문에 공유수면·해상·앞바다·km·위경도 DMS 표지
  육상풍력 : 해상 표지가 전혀 없고 육상 지번(산·리+번지)만 존재
  풍력_구분미상 : 둘 다 아님 (읍면동만 있고 지번 없음 등) → 검수 유지
"""

from __future__ import annotations

import re

OFFSHORE_RE = re.compile(r"공유수면|해상|해역|앞바다|해면|먼바다|지선|\d+\s*km|"
                         r"[동서남북]측|\d+°\s*\d+['′]")
LAND_JIBUN_RE = re.compile(r"산\s*\d|[가-힣]+리\s*\d|[가-힣]+동\s*\d|[가-힣]+길\s*\d|"
                           r"\d+번지|\d+필지|-\s*\d")

# 사용자(발전사업 실무자)가 육상풍력으로 확인한 사업 (2026-09-09).
# 읍면동 단위 주소만 있어 자동 판정이 불가했으나 실무 확인으로 육상 확정.
USER_CONFIRMED_ONSHORE = (
    "고흥풍력", "대명지이씨", "영광백수풍력", "자은주민바람",
    "영광약수풍력", "도경풍력", "아일랜드발전", "신흥풍력", "에이치원에너",
)


def reclassify(location: str, coord_raw: str = "", name: str = "") -> tuple[str, str]:
    """(발전원, 근거). 재분류 불가 시 ('풍력_구분미상', 사유)."""
    blob = f"{location} {coord_raw}"
    if OFFSHORE_RE.search(blob):
        token = OFFSHORE_RE.search(blob).group()
        return "해상풍력", f"위치/좌표 원문 해상 표지: '{token}'"
    if LAND_JIBUN_RE.search(location or ""):
        return "육상풍력", "육상 지번만 존재, 해상 표지 없음 (원문 지번 근거)"
    compact = re.sub(r"\s+", "", name or "")
    if any(tok in compact for tok in USER_CONFIRMED_ONSHORE):
        return "육상풍력", "사용자(실무자) 확인 — 육상풍력 (2026-09-09)"
    return "풍력_구분미상", "읍면동 단위만 존재, 육상/해상 미상 — 검수필요"
