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

from .normalize import normalize_company

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


def match_ledger_rows(project: dict, ledger_rows: list[dict]) -> tuple[list[dict], str, str]:
    """한 사업에 해당하는 허가대장 행들을 고른다.

    반환: (매칭행, 매칭근거, 신뢰도)
    """
    sigungu = (project.get("시군구") or "").strip()
    if not sigungu:
        return [], "", "낮음"

    company_key = _name_tokens(project.get("사업주체", ""))
    name_key = _name_tokens(project.get("발전소명", ""))
    capacity = project.get("설비용량_MW")
    capacity = float(capacity) if capacity not in (None, "", "nan") else None

    by_company, by_name, by_capacity = [], [], []
    for row in ledger_rows:
        if (row.get("시군구") or "").strip() != sigungu:
            continue                       # 시군구가 다르면 후보로 보지 않는다
        ledger_key = _name_tokens(row.get("사업자", ""))
        if not ledger_key:
            continue

        if company_key and fuzz.ratio(company_key, ledger_key) >= COMPANY_MATCH_THRESHOLD:
            by_company.append(row)
            continue
        if name_key and fuzz.partial_ratio(name_key, ledger_key) >= NAME_MATCH_THRESHOLD:
            by_name.append(row)
            continue
        row_capacity = row.get("설비용량_MW_표준")
        if capacity is not None and row_capacity not in (None, "", "nan"):
            if abs(float(row_capacity) - capacity) <= CAPACITY_TOLERANCE_MW:
                by_capacity.append(row)

    if by_company:
        return by_company, "사업주체명 일치 + 시군구 일치", "높음"
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
