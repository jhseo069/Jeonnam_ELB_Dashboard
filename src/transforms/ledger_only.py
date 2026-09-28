"""허가대장에만 있는 사업을 발전소 단위로 편입한다.

최근 5년 회의록 모집단에는 없지만 3MW초과 허가대장(2001~)에는 있는 사업들이다.
대부분 2021년 이전 최초허가라 회의록 수집 범위 밖이다. 사용자 결정에 따라
데이터상태='잠정(허가대장)'으로 편입한다.

허가대장은 사업 단위가 아니라 '허가·변경 1건 = 1행'이므로, 사업자×시군구로 묶어
발전소 1건을 만든다. 회의록에서 나오는 사업주체·최대주주·총사업비는 없으므로
빈 채로 두고(추정하지 않음), 위치·용량·최초허가일만 채운다.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from .dedup import name_core
from .merge import make_project_id, sequence_signature

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


def _compact(text) -> str:
    """잘림 비교용: 공백·법인표기만 지운 원문(지역명 등은 남긴다)."""
    return re.sub(r"[\s㈜()（）]|주식회사", "", str(text or ""))


def _caps(rows: list[dict]) -> set[float]:
    out = set()
    for r in rows:
        try:
            v = float(r.get("설비용량_MW_표준"))
        except (TypeError, ValueError):
            continue
        if v == v:
            out.add(round(v, 2))
    return out


def build_ledger_only_projects(master: list[dict], ledger: list[dict]) -> list[dict]:
    """통합목록에 없는 허가대장 사업을 발전소 레코드 목록으로 만든다."""
    matched_keys = set()
    past_keys: list[tuple[str, str, float]] = []
    alias_caps: list[tuple[str, str, float]] = []   # 회의록 사업명 핵심·용량
    for project in master:
        sigungu = project.get("시군구") or ""
        for alias in str(project.get("발전소명_원문") or "").split(" | "):
            if alias:
                matched_keys.add((name_core(alias), sigungu))
        matched_keys.add((name_core(str(project.get("사업주체") or "")), sigungu))
        # 과거 사업주체(최초허가 당시 상호)는 용량이 맞을 때만 같은 사업으로 본다
        # (enrich.match_ledger_rows 와 같은 조건 — 같은 회사의 다른 사업을 지우지 않게).
        try:
            cap = float(project.get("설비용량_MW"))
            cap = None if cap != cap else cap
        except (TypeError, ValueError):
            cap = None
        for past in str(project.get("사업주체_이력") or "").split(" | "):
            if past and cap is not None:
                past_keys.append((name_core(past), sigungu, cap))
        if cap is not None:
            for alias in str(project.get("발전소명_원문") or "").split(" | "):
                if alias:
                    alias_caps.append((_compact(alias), sigungu, cap))

    def already_covered(company_core: str, sigungu: str, rows: list[dict]) -> bool:
        if any(mk[1] == sigungu and mk[0] and
               fuzz.ratio(mk[0], company_core) >= COMPANY_MATCH for mk in matched_keys):
            return True
        for core, sgg, cap in past_keys:
            if sgg == sigungu and core and fuzz.ratio(core, company_core) >= COMPANY_MATCH:
                if any(r.get("설비용량_MW_표준") not in (None, "", "nan")
                       and abs(float(r["설비용량_MW_표준"]) - cap) <= max(1.0, cap * 0.02)
                       for r in rows):
                    return True
        # 허가대장 상호가 잘려('진도그린태', '밝은고흥태양') 회의록 사업명의 앞부분만 남은
        # 경우: 앞부분 4자 이상 일치 + 같은 시군구 + 용량 일치면 이미 있는 사업이다.
        head = _compact(rows[0].get("사업자_표준") or rows[0].get("사업자"))
        if len(head) >= 4:
            caps = _caps(rows)
            for core, sgg, cap in alias_caps:
                if sgg == sigungu and core.startswith(head) and \
                        any(abs(c - cap) <= max(0.05, cap * 0.005) for c in caps):
                    return True
        return False

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

    # 허가대장 PDF 줄바꿈으로 상호가 잘려 같은 회사가 두 묶음이 된 경우
    # ('이촌태양광발'/'이촌태양광발 전', '에이치원에너'/'에이치원에너 지') 합친다.
    # 앞부분 4자 이상 일치 + 같은 시군구 + 용량 일치가 모두 맞을 때만.
    def raw(key):
        return _compact(groups[key][0].get("사업자_표준") or groups[key][0].get("사업자"))

    for short in sorted(groups, key=lambda k: len(raw(k))):
        if short not in groups or len(raw(short)) < 4:
            continue
        for long_ in list(groups):
            if long_ == short or long_[1] != short[1] or not raw(long_).startswith(raw(short)):
                continue
            # '안좌스마트팜앤쏠라시티' ⊂ '…쏠라시티2' 처럼 뒤에 순번이 붙으면 별개 사업
            if sequence_signature(raw(long_)) != sequence_signature(raw(short)):
                continue
            if _caps(groups[short]) & _caps(groups[long_]):
                groups[long_] += groups.pop(short)
                break

    new_projects = []
    for (company_core, sigungu), rows in groups.items():
        if already_covered(company_core, sigungu, rows):
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
