"""풍력 사업의 육상/해상 구분.

허가대장은 원동력을 '풍력'으로만 적는 경우가 많아 그 자체로는 갈 수 없다.
다음 순서로 원문 근거를 찾고, 어느 것도 걸리지 않으면 판정하지 않는다.

  1. 사업명 표기          '영광 야월 해상풍력 발전사업'
  2. KPX 추진현황 형식     '해상 풍력' / '육상 풍력'
  3. 허가위치 원문 표기    '인근 해상', '공유수면', '해역', '앞바다'
  4. 사업주체 법인명 표기  '매월해상풍력㈜'

용량이나 지리적 추정으로 가르지 않는다(지침 §22). 근거가 없으면 '풍력_구분미상'을
유지하고 검수로 넘긴다.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# 위치 원문에 나타나는 해상 표지. 육상 지번과 함께 적히는 경우가 있어
# '해상' 계열이 하나라도 있으면 해상으로 본다(해상풍력의 육상 사무소 주소 병기 사례).
OFFSHORE_LOCATION_TOKENS = ("인근 해상", "인근해상", "해상", "해역", "앞바다",
                            "공유수면", "먼바다", "해면")
ONSHORE_LOCATION_TOKENS = ("산", "리 산", "임야")     # 단독으로는 근거가 약하다

NAME_MATCH_THRESHOLD = 90

# 비교에서 걷어낼 지역명·일반어. 같은 시군구 안에서는 모두가 공유하므로
# 남겨 두면 서로 다른 사업이 높은 유사도로 잘못 붙는다.
GENERIC_TOKENS = (
    "전라남도", "전남광주통합특별시", "전남", "광주광역시", "광주",
    "목포시", "여수시", "순천시", "나주시", "광양시",
    "담양군", "곡성군", "구례군", "고흥군", "보성군", "화순군", "장흥군", "강진군",
    "해남군", "영암군", "무안군", "함평군", "영광군", "장성군", "완도군", "진도군", "신안군",
    "목포", "여수", "순천", "나주", "광양", "담양", "곡성", "구례", "고흥", "보성",
    "화순", "장흥", "강진", "해남", "영암", "무안", "함평", "영광", "장성", "완도",
    "진도", "신안",
    "해상풍력", "육상풍력", "풍력발전소", "풍력발전", "풍력",
    "발전사업", "발전소", "발전", "주식회사", "㈜", "(주)", "단지", "단계",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def from_project_name(names: list[str]) -> tuple[str | None, str]:
    compact = _compact(" ".join(names))
    if "해상풍력" in compact:
        return "해상풍력", "사업명 표기: 해상풍력"
    if "육상풍력" in compact:
        return "육상풍력", "사업명 표기: 육상풍력"
    return None, ""


def from_company_name(company: str) -> tuple[str | None, str]:
    compact = _compact(company)
    if "해상풍력" in compact:
        return "해상풍력", f"사업주체 법인명 표기: {company}"
    if "육상풍력" in compact:
        return "육상풍력", f"사업주체 법인명 표기: {company}"
    return None, ""


def distinctive_part(text: str) -> str:
    """지역명과 일반 명사를 걷어낸 고유 부분만 남긴다.

    같은 시군구 안에서는 '영광', '풍력', '발전사업'을 모두 공유하므로 그대로 비교하면
    서로 다른 사업이 높은 점수로 붙는다(예: '영광 염산풍력' vs '영광영백 풍력발전소').
    지역·일반어를 지운 뒤 남은 고유부로만 비교한다.
    """
    compact = _compact(text)
    for token in GENERIC_TOKENS:
        compact = compact.replace(token, "")
    return compact


def from_kpx(project: dict, kpx_rows: list[dict]) -> tuple[str | None, str]:
    """KPX 추진현황의 '형식' 칼럼으로 가른다. 시군구가 같고 고유부가 일치해야 잇는다."""
    sigungu = (project.get("시군구") or "").strip()
    name_core = distinctive_part(project.get("발전소명", ""))
    company_core = distinctive_part(project.get("사업주체", ""))
    # 지역명·일반어를 걷어내고 남은 고유부가 너무 짧으면(예: '영암풍력'→'', '~의'→'의')
    # 매칭하지 않는다. 빈/찌꺼기 고유부끼리 100점으로 붙는 오매칭을 막는다.
    if len(name_core.replace("의", "")) < 2:
        return None, ""

    for row in kpx_rows:
        source = (row.get("발전원_표준") or "").strip()
        if source not in ("해상풍력", "육상풍력"):
            continue
        location = f"{row.get('발전소명','')} {row.get('발전회사','')}"
        if sigungu and sigungu[:-1] not in location and sigungu not in location:
            continue                       # 시군구 표기가 전혀 없으면 건너뛴다

        candidate_core = distinctive_part(row.get("발전소명", ""))
        company_candidate = distinctive_part(row.get("발전회사", ""))
        if len(candidate_core) < 2:
            continue
        score = max(fuzz.ratio(name_core, candidate_core),
                    fuzz.ratio(company_core, company_candidate) if company_core else 0)
        if score >= NAME_MATCH_THRESHOLD:
            return source, (f"KPX 추진현황 형식='{row.get('발전원')}' "
                            f"(발전소명 '{row.get('발전소명')}' 고유부 유사도 {score:.0f})")
    return None, ""


def from_location(location: str) -> tuple[str | None, str]:
    """위치 원문의 해상 표지로 해상 판정. 해상 표지가 하나라도 있으면 해상으로 본다.

    해상풍력이 육상 사무소 주소를 병기하는 사례가 있어(예: '제원리 산358번지 지선'),
    육상 지번이 함께 있어도 해상 표지가 있으면 해상으로 판정한다.
    """
    text = location or ""
    for token in OFFSHORE_LOCATION_TOKENS:
        if token in text:
            return "해상풍력", f"허가위치 원문 표기: '{token}'"
    return None, ""


# 육상 지번·도로명 표지. 해상 표지가 전무할 때만 육상 근거로 쓴다.
LAND_JIBUN_RE = re.compile(r"산\s*\d|[가-힣]+리\s*\d|[가-힣]+동\s*\d+|[가-힣]+길\s*\d|\d+번지|\d+필지|-\s*\d")


def from_land_jibun(location: str) -> tuple[str | None, str]:
    """육상 지번만 있고 해상 표지가 전혀 없으면 육상으로 판정한다.

    지번 형태는 지리적 추정이 아니라 원문에 기재된 사실이므로 근거로 삼는다(사용자 확인).
    다만 해상풍력이 육상 지번을 병기하는 경우가 있으므로, from_location(해상 표지)이
    먼저 실패한 뒤에만 이 판정에 도달하도록 resolve 의 호출 순서를 지킨다.
    """
    text = location or ""
    if any(token in text for token in OFFSHORE_LOCATION_TOKENS):
        return None, ""                       # 해상 표지가 있으면 여기서 판정하지 않는다
    if LAND_JIBUN_RE.search(text):
        return "육상풍력", "육상 지번만 존재하고 해상 표지 없음 (원문 지번 근거)"
    return None, ""


def resolve(project: dict, kpx_rows: list[dict]) -> tuple[str, str]:
    """(발전원, 근거)를 돌려준다. 근거가 없으면 원래 값을 그대로 유지한다."""
    current = project.get("발전원", "")
    if current != "풍력_구분미상":
        return current, project.get("발전원_근거", "")

    names = str(project.get("발전소명_원문") or project.get("발전소명") or "").split(" | ")
    location = project.get("허가위치_원문", "")
    for resolver in (
        lambda: from_project_name(names),
        lambda: from_kpx(project, kpx_rows),
        lambda: from_company_name(project.get("사업주체", "")),
        lambda: from_location(location),          # 해상 표지 우선
        lambda: from_land_jibun(location),        # 해상 표지 전무 + 육상 지번 → 육상
    ):
        result, basis = resolver()
        if result:
            return result, basis

    return "풍력_구분미상", "원문에 육상/해상 표기 없음 — 검수필요"
