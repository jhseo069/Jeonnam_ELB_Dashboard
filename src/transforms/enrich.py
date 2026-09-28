"""허가대장·KPX 값을 사업 대표정보에 얹는다.

원칙(사용자 확인 + 지침 §4):
  - 최초허가일과 허가 변경이력의 정본은 전기위원회 자료(3MW초과 허가대장)다.
  - KPX 추진현황은 사업자 자율제출 취합자료이므로 보조 출처로만 쓴다.
  - 이름이 비슷하다는 이유만으로 잇지 않는다. 시군구가 같아야 후보로 보고,
    사업자명 유사도나 설비용량 일치 같은 별도 근거가 있어야 연결한다.
  - 연결 근거와 신뢰도를 함께 남겨 검수에서 되짚을 수 있게 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .normalize import classify_region, normalize_company, split_location_change

COMPANY_MATCH_THRESHOLD = 90      # 사업자명 유사도 기준
CAPACITY_TOLERANCE_MW = 0.05      # 용량 일치로 볼 오차
NAME_MATCH_THRESHOLD = 92         # 사업명 <-> 사업자명 유사도 기준


@dataclass
class LedgerMatch:
    프로젝트ID: str
    매칭행수: int
    최초발전사업허가일: str | None
    최초허가일_출처페이지: str
    허가변경일_목록: str
    원동력_원문: str
    발전원_허가대장: str
    매칭근거: str
    매칭신뢰도: str


def _name_tokens(text: str) -> str:
    """비교용으로 공백·법인격 표기를 지운 문자열."""
    return re.sub(r"\s+", "", normalize_company(text or "")).lower()


def project_sigungus(project: dict) -> set[str]:
    """사업이 거쳐간 시군구: 현재 + 위치 변경 전('신안 … → 진도 …'의 신안).

    허가대장은 행마다 당시 위치로 적혀 있어(장병도: 신안 → 2024-11 진도) 현재
    시군구만으로 대조하면 변경 전 행을 놓친다.
    """
    out = {(project.get("시군구") or "").strip()}
    before, _ = split_location_change(str(project.get("허가위치_원문") or ""))
    out.add(classify_region(before)[1])
    out.discard("")
    return out


def _family(source) -> str:
    """발전원 계열: 태양광 / 풍력(육상·해상·미상). 그 외·미상은 빈 문자열."""
    s = str(source or "")
    if "태양광" in s:
        return "태양광"
    if "풍력" in s:
        return "풍력"
    return ""


def match_ledger_rows(project: dict, ledger_rows: list[dict]) -> tuple[list[dict], str, str]:
    """한 사업에 해당하는 허가대장 행들을 고른다.

    반환: (매칭행, 매칭근거, 신뢰도)
    """
    sigungu = (project.get("시군구") or "").strip()
    if not sigungu:
        return [], "", "낮음"
    sigungus = project_sigungus(project)

    company_key = _name_tokens(project.get("사업주체", ""))
    # 심의 이력상 과거 사업주체(최초허가 당시 상호·SPC 전환 전 모회사). 허가대장은 최초
    # 허가 당시 상호로 적혀 있어 최신 사업주체만으로는 최초허가 행을 못 찾는다.
    past_keys = {_name_tokens(c) for c in
                 str(project.get("사업주체_이력") or "").split(" | ") if c}
    past_keys.discard("")
    past_keys.discard(company_key)
    name_key = _name_tokens(project.get("발전소명", ""))
    capacity = project.get("설비용량_MW")
    capacity = float(capacity) if capacity not in (None, "", "nan") else None

    by_company, by_name, by_capacity = [], [], []
    by_past: dict[str, list[dict]] = {}          # 과거 사업주체 매칭(허가대장 상호별)
    for row in ledger_rows:
        if (row.get("시군구") or "").strip() not in sigungus:
            continue                       # 시군구가 다르면 후보로 보지 않는다
        # 같은 사업자가 한 시군구에 태양광·풍력을 함께 가진 경우(대한그린에너지: 영광
        # 염산 풍력 + 백수 태양광) 다른 발전원 사업의 허가일이 섞이지 않게 한다.
        if _family(project.get("발전원")) and _family(row.get("발전원_표준")) \
                and _family(project.get("발전원")) != _family(row.get("발전원_표준")):
            continue
        ledger_key = _name_tokens(row.get("사업자", ""))
        if not ledger_key:
            continue

        if company_key and fuzz.ratio(company_key, ledger_key) >= COMPANY_MATCH_THRESHOLD:
            by_company.append(row)
            continue
        if any(fuzz.ratio(k, ledger_key) >= COMPANY_MATCH_THRESHOLD for k in past_keys):
            by_past.setdefault(ledger_key, []).append(row)
            continue
        if name_key and fuzz.partial_ratio(name_key, ledger_key) >= NAME_MATCH_THRESHOLD:
            by_name.append(row)
            continue
        row_capacity = row.get("설비용량_MW_표준")
        if capacity is not None and row_capacity not in (None, "", "nan"):
            if abs(float(row_capacity) - capacity) <= CAPACITY_TOLERANCE_MW:
                by_capacity.append(row)

    # 과거 사업주체 매칭은 그 상호의 허가대장 행 중 용량이 이 사업과 일치하는 게 있을
    # 때만 쓴다. 같은 회사의 다른 사업(대한그린에너지 염산 49.8MW ≠ 영광풍력 79.6MW)을
    # 배제하면서, 용량이 변경된 동일 사업(신안어의 16→99MW)은 행 전체를 살린다.
    past_rows = []
    for rows in by_past.values():
        if capacity is not None and any(
                row.get("설비용량_MW_표준") not in (None, "", "nan")
                and abs(float(row["설비용량_MW_표준"]) - capacity) <= max(1.0, capacity * 0.02)
                for row in rows):
            past_rows += rows
    if by_company or past_rows:
        return by_company + past_rows, "사업주체명 일치 + 시군구 일치", "높음"
    if by_name:
        return by_name, "사업명-사업자명 일치 + 시군구 일치", "보통"
    if by_capacity:
        return by_capacity, "설비용량 일치 + 시군구 일치 (명칭 불일치)", "낮음"
    return [], "", "자료없음"


def pick_first_permit_date(rows: list[dict]) -> tuple[str | None, str]:
    """최초허가일을 고른다.

    허가대장은 '기타(변경사항)'가 빈칸인 행이 최초 허가건이다(문서 자체 안내).
    최초허가 행이 여럿이면 가장 이른 날짜를 쓴다. 최초허가 행이 없으면
    변경 행의 최소 날짜를 최초허가일로 올려쓰지 않고 None 을 돌려준다
    (변경허가일을 최초허가일로 쓰지 않는다 — 지침 §22).
    """
    first_rows = [r for r in rows
                  if (r.get("이력구분") == "최초허가") and r.get("허가변경일_표준")]
    if not first_rows:
        return None, ""
    chosen = min(first_rows, key=lambda r: str(r["허가변경일_표준"]))
    return str(chosen["허가변경일_표준"]), str(chosen.get("출처페이지", ""))


def resolve_wind_type(rows: list[dict]) -> tuple[str | None, str]:
    """허가대장 원동력 표기로 육상/해상을 가른다. 표기가 없으면 판정하지 않는다."""
    labels = {r.get("발전원_표준") for r in rows}
    if "해상풍력" in labels:
        return "해상풍력", "허가대장 원동력 표기: 해상"
    if "육상풍력" in labels:
        return "육상풍력", "허가대장 원동력 표기: 육상"
    return None, ""


def enrich_with_ledger(projects: list[dict], ledger_rows: list[dict]) -> list[dict]:
    """사업 대표정보에 허가대장 값을 얹은 새 목록을 돌려준다."""
    enriched = []
    for project in projects:
        record = dict(project)
        rows, basis, confidence = match_ledger_rows(project, ledger_rows)

        first_date, page = pick_first_permit_date(rows)
        record["최초발전사업허가일"] = first_date
        record["최초허가일_출처"] = (
            f"3MW초과 발전사업 허가대장 p{page}" if first_date else "")
        record["허가대장_매칭행수"] = len(rows)
        record["허가대장_매칭근거"] = basis
        record["허가대장_매칭신뢰도"] = confidence
        record["허가대장_원동력_원문"] = " | ".join(
            sorted({r.get("원동력", "") for r in rows if r.get("원동력")}))
        record["허가변경일_목록"] = " | ".join(
            sorted({str(r["허가변경일_표준"]) for r in rows if r.get("허가변경일_표준")}))

        if record.get("발전원") == "풍력_구분미상":
            resolved, reason = resolve_wind_type(rows)
            if resolved:
                record["발전원"] = resolved
                record["발전원_근거"] = reason

        # 좌표 원문(해상풍력 사업구역 표기)이 허가대장에 있으면 옮겨 둔다
        coords = [r.get("좌표_원문", "") for r in rows if r.get("좌표_원문")]
        record["허가대장_좌표_원문"] = " | ".join(coords)[:500]

        enriched.append(record)
    return enriched
