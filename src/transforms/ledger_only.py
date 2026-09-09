"""허가대장에만 있는 사업을 발전소 단위로 편입한다.

최근 5년 회의록 모집단에는 없지만 3MW초과 허가대장(2001~)에는 있는 사업들이다.
대부분 2021년 이전 최초허가라 회의록 수집 범위 밖이다. 사용자 결정에 따라
데이터상태='잠정(허가대장)'으로 편입한다.

허가대장은 사업 단위가 아니라 '허가·변경 1건 = 1행'이므로, 사업자×시군구로 묶어
발전소 1건을 만든다. 회의록에서 나오는 사업주체·최대주주·총사업비는 없으므로
빈 채로 두고(추정하지 않음), 위치·용량·최초허가일만 채운다.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from .dedup import name_core
from .merge import make_project_id

COMPANY_MATCH = 80


def _first_permit(rows: list[dict]) -> tuple[str | None, str]:
    firsts = [r for r in rows
              if r.get("이력구분") == "최초허가" and r.get("허가변경일_표준")]
    if not firsts:
        return None, ""
    chosen = min(firsts, key=lambda r: str(r["허가변경일_표준"]))
    return str(chosen["허가변경일_표준"]), str(chosen.get("출처페이지", ""))


def _representative_capacity(rows: list[dict]) -> float | None:
    """가장 최근(허가변경일 최대) 행의 용량을 대표로 쓴다."""
    dated = [r for r in rows if r.get("허가변경일_표준") and r.get("설비용량_MW_표준")]
    if dated:
        latest = max(dated, key=lambda r: str(r["허가변경일_표준"]))
        return float(latest["설비용량_MW_표준"])
    for r in rows:
        if r.get("설비용량_MW_표준"):
            return float(r["설비용량_MW_표준"])
    return None


def build_ledger_only_projects(master: list[dict], ledger: list[dict]) -> list[dict]:
    """통합목록에 없는 허가대장 사업을 발전소 레코드 목록으로 만든다."""
    matched_keys = set()
    for project in master:
        sigungu = project.get("시군구") or ""
        for alias in str(project.get("발전소명_원문") or "").split(" | "):
            if alias:
                matched_keys.add((name_core(alias), sigungu))
        matched_keys.add((name_core(str(project.get("사업주체") or "")), sigungu))

    def already_covered(company_core: str, sigungu: str) -> bool:
        return any(mk[1] == sigungu and mk[0] and
                   fuzz.ratio(mk[0], company_core) >= COMPANY_MATCH
                   for mk in matched_keys)

    # 사업자×시군구로 허가대장 행 묶기
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in ledger:
        source = row.get("발전원_표준")
        if source not in ("태양광", "육상풍력", "해상풍력", "풍력_구분미상"):
            continue
        company_core = name_core(str(row.get("사업자_표준") or ""))
        sigungu = row.get("시군구") or ""
        if not company_core or not sigungu:
            continue
        groups.setdefault((company_core, sigungu), []).append(row)

    new_projects = []
    for (company_core, sigungu), rows in groups.items():
        if already_covered(company_core, sigungu):
            continue

        representative = rows[0]
        name = str(representative.get("사업자") or "").strip()
        first_date, page = _first_permit(rows)
        capacity = _representative_capacity(rows)
        sources = {r.get("발전원_표준") for r in rows}
        source = ("해상풍력" if "해상풍력" in sources else
                  "육상풍력" if "육상풍력" in sources else
                  "태양광" if "태양광" in sources else "풍력_구분미상")

        region = "광주" if sigungu in ("동구", "서구", "남구", "북구", "광산구") else "전남"
        coord_raw = " | ".join(r.get("좌표_원문", "") for r in rows if r.get("좌표_원문"))

        new_projects.append({
            "프로젝트ID": make_project_id(name, sigungu),
            "발전소명": name,
            "발전소명_원문": name,
            "발전원": source,
            "발전원_근거": f"허가대장 원동력: {representative.get('원동력', '')}",
            "지역": region,
            "시군구": sigungu,
            "사업주체": name,                      # 허가대장 상호 = 사업자
            "최대주주": "",                        # 회의록 없음 → 추정 안 함
            "허가위치_원문": str(representative.get("위치") or ""),
            "설비용량_MW": capacity,
            "총사업비_억원": None,
            "최초발전사업허가일": first_date,
            "최초허가일_출처": f"3MW초과 발전사업 허가대장 p{page}" if first_date else "",
            "최근전기위원회회차": None,
            "최근안건유형": "",
            "데이터상태": "잠정(허가대장)",
            "대표출처": "3MW초과 발전사업 허가대장",
            "출처페이지": page,
            "허가대장_매칭신뢰도": "허가대장전용",
            "허가대장_좌표_원문": coord_raw[:500],
            "이력건수": len(rows),
            "편입경로": "허가대장전용",
        })
    return new_projects
