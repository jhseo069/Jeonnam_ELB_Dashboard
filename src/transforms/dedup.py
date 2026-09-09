"""동일 사업 판정과 중복 처리.

회의록 안건명은 같은 사업이라도 표기가 흔들린다.
  '기림아이(주)의 영광 영백풍력' / '전남 영광 영백풍력'
  '영광 염산풍력 발전사업 변경' / '영광 염산풍력 발전사업 계통연계점 변경'
사업명만 비교하면 이런 것들이 갈라져 중복 집계된다.

지침 §13에 따라 사업명이 비슷하다는 이유만으로 합치지 않는다. 대신 사업주체·위치·용량
같은 독립 근거가 겹칠 때만 병합하고, 근거가 부분적이면 `중복의심`으로 남겨 검수에 넘긴다.

  자동병합 : 시군구 + 정규화 사업주체 + 위치 지번핵심 + 용량이 모두 일치
  중복의심 : 위 조건 중 둘만 일치 (예: 사업주체와 위치는 같은데 용량이 다름)
"""

from __future__ import annotations

import re
from collections import defaultdict

from rapidfuzz import fuzz

from .normalize import normalize_company
from .wind_type import GENERIC_TOKENS as NAME_GENERIC_TOKENS

# 위치 원문에서 '두우리 1070-2' 같은 리/번지 핵심만 뽑는다.
LOCATION_CORE_RE = re.compile(r"([가-힣]+(?:리|동|읍|면))\s*(\d+(?:-\d+)?)?")
CAPACITY_TOLERANCE = 0.01
NAME_GUARD_THRESHOLD = 70   # 사업명 고유부가 이보다 안 닮으면 병합하지 않는다


def location_core(location: str) -> str:
    """위치 원문에서 비교용 핵심(최말단 행정구역 + 지번)만 뽑는다.

    '전남 영광군 염산면 두우리 1070-2 등 6개 필지' -> '두우리1070-2'
    '전남 영광군 염산면 두우리 1105번지 일원'      -> '두우리1105'
    '일원', '등 OO필지' 같은 표현은 비교에서만 무시하고 원문은 그대로 보존한다.

    지번이 없으면 빈 문자열을 돌려준다. '조도면'처럼 면 단위까지만 남으면
    같은 면에 있는 서로 다른 해상풍력이 전부 한 덩어리로 묶여 버린다.
    """
    if not location:
        return ""
    matches = LOCATION_CORE_RE.findall(location)
    numbered = [(name, number) for name, number in matches if number]
    if not numbered:
        return ""
    name, number = numbered[-1]
    return f"{name}{number}"


def name_core(text: str) -> str:
    """사업명 비교용 고유부. 지역명·일반어를 걷어낸다."""
    compact = re.sub(r"\s+", "", text or "")
    for token in NAME_GENERIC_TOKENS:
        compact = compact.replace(token, "")
    return compact


def _capacity(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def signature(project: dict) -> tuple[str, str, str, str]:
    """(시군구, 정규화 사업주체, 위치 핵심, 용량문자열)."""
    capacity = _capacity(project.get("설비용량_MW"))
    return (
        (project.get("시군구") or "").strip(),
        normalize_company(project.get("사업주체", "")),
        location_core(project.get("허가위치_원문", "")),
        f"{capacity:.2f}" if capacity is not None else "",
    )


def find_duplicate_groups(projects: list[dict]) -> tuple[dict[str, list[str]], list[dict]]:
    """자동 병합할 묶음과 검수로 넘길 중복의심 목록을 함께 돌려준다."""
    exact: dict[tuple, list[dict]] = defaultdict(list)
    for project in projects:
        sigungu, company, core, capacity = signature(project)
        if sigungu and company and core and capacity:
            exact[(sigungu, company, core, capacity)].append(project)

    merge_groups: dict[str, list[str]] = {}
    merged_ids: set[str] = set()
    for key, members in exact.items():
        if len(members) < 2:
            continue
        # 사업주체·위치·용량이 같아도 사업명 고유부가 전혀 다르면 병합하지 않는다.
        # 회의록 2단 파싱에서 필드가 옆 안건으로 새면 이런 조합이 만들어진다.
        cores = [name_core(m.get("발전소명", "")) for m in members]
        if any(fuzz.partial_ratio(cores[0], c) < NAME_GUARD_THRESHOLD for c in cores[1:]):
            continue
        # 대표는 이력이 가장 많은 건, 같으면 사업명이 짧은 쪽(군더더기가 적다)
        members.sort(key=lambda p: (-int(p.get("이력건수") or 0),
                                    len(str(p.get("발전소명") or ""))))
        keeper = members[0]["프로젝트ID"]
        merge_groups[keeper] = [m["프로젝트ID"] for m in members[1:]]
        merged_ids.update(merge_groups[keeper])

    # 부분 일치: 사업주체 + 위치는 같은데 용량이 다른 경우 등
    partial: dict[tuple, list[dict]] = defaultdict(list)
    for project in projects:
        if project["프로젝트ID"] in merged_ids:
            continue
        sigungu, company, core, _ = signature(project)
        if sigungu and core:
            partial[(sigungu, core)].append(project)

    suspects: list[dict] = []
    for (sigungu, core), members in partial.items():
        if len(members) < 2:
            continue
        ids = [m["프로젝트ID"] for m in members]
        for member in members:
            others = [i for i in ids if i != member["프로젝트ID"]]
            suspects.append({
                "프로젝트ID": member["프로젝트ID"],
                "검수항목": "동일 사업 여부",
                "현재값": member.get("발전소명", ""),
                "충돌값": " / ".join(
                    m.get("발전소명", "") for m in members
                    if m["프로젝트ID"] != member["프로젝트ID"]),
                "검수사유": (f"시군구({sigungu})와 위치 핵심({core})이 같으나 "
                          f"사업주체 또는 용량이 달라 자동 병합하지 않음"),
                "관련출처": member.get("대표출처", ""),
                "권장값": "원문 대조 후 동일 사업이면 병합",
                "처리상태": "대기",
                "중복후보": " / ".join(others),
            })
    return merge_groups, suspects


def apply_merges(projects: list[dict], merge_groups: dict[str, list[str]]) -> list[dict]:
    """병합 대상을 대표 사업에 흡수시킨다. 흡수된 사업명은 별칭으로 보존한다."""
    absorbed = {pid: keeper for keeper, members in merge_groups.items() for pid in members}
    by_id = {p["프로젝트ID"]: p for p in projects}

    for pid, keeper_id in absorbed.items():
        source, keeper = by_id[pid], by_id[keeper_id]
        aliases = str(keeper.get("발전소명_원문") or "").split(" | ")
        aliases += str(source.get("발전소명_원문") or source.get("발전소명") or "").split(" | ")
        keeper["발전소명_원문"] = " | ".join(dict.fromkeys(a for a in aliases if a))
        keeper["이력건수"] = int(keeper.get("이력건수") or 0) + int(source.get("이력건수") or 0)
        keeper["병합된ID"] = " / ".join(
            filter(None, [str(keeper.get("병합된ID") or ""), pid]))
        # 대표값은 더 최근 회차 쪽을 남긴다
        keeper_round = keeper.get("최근전기위원회회차") or 0
        source_round = source.get("최근전기위원회회차") or 0
        if source_round > keeper_round:
            for column in ("사업주체", "최대주주", "허가위치_원문", "설비용량_MW",
                           "총사업비_억원", "최근전기위원회회차", "최근안건유형",
                           "대표출처", "출처페이지"):
                if source.get(column) not in (None, "", "nan"):
                    keeper[column] = source[column]

    return [p for p in projects if p["프로젝트ID"] not in absorbed]
