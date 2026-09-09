"""송배전이용계약(계통연계) 정보 추출.

두 출처에서 성격이 다른 정보가 나온다. 섞으면 안 된다(지침 §14).

  KPX 상세면 '추진 현황' 이력표
    - '송배전이용계약 최초체결  '20. 11'  -> 실제 체결일. 계약상태=체결
    - '송배전이용계약 최초체결  -'         -> 미체결. 날짜로 넣지 않는다
    - '송배전이용계약 최초체결  (\\'27. 01)' -> 괄호는 예정. 예정여부=예정, 상태=협의중
    - '접속제의서 제출', '이용신청'          -> 신청 단계. 체결로 기록하지 않는다

  회의록/개최결과 허가조건
    - '계통 보강(\\'34.10월 예정) 이후 연계가능' -> 계약이 아니라 허가조건·계통제약.
      계통계약 시트가 아니라 사업 대표정보의 허가조건으로 간다.

규칙:
  - 자료에 없는 연계전압/변전소를 설비용량으로 추정하지 않는다.
  - 신청·접속제의서·협의 중을 체결로 올리지 않는다.
  - 계통보강 예정일을 계약체결일로 쓰지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

# '송배전이용계약 최초체결  '20. 11'  /  '송배전 이용계약 최초체결  -'
CONTRACT_LINE_RE = re.compile(
    r"송\s*[·•]?\s*배\s*전\s*이?\s*용?\s*계약\s*(최초체결|변경|체결)?\s*(.*)")
# 값에서 날짜, 미체결, 예정(괄호)을 가려낸다
DATE_RE = re.compile(r"[''`‘’]?\s*(\d{2})\s*\.\s*(\d{1,2})")
PLANNED_RE = re.compile(r"\(\s*[''`‘’]?\s*\d{2}\s*\.\s*\d{1,2}\s*\)")
VOLTAGE_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:kV|㎸)")
SUBSTATION_RE = re.compile(r"([가-힣]+)\s*(?:변전소|S/S|s/s|개폐소)")

NO_CONTRACT_MARKS = {"-", "", "–", "—", "미체결", "없음"}

CONTRACT_STATUS = {
    "체결": "체결", "최초체결": "체결", "변경": "변경",
    "접속제의서": "접속제의서 제출", "이용신청": "신청", "신청": "신청",
    "협의": "협의 중",
}


@dataclass
class GridContract:
    프로젝트ID: str = ""
    계약상태: str = "자료없음"
    최초체결일: str | None = None
    최근변경일: str | None = None
    계약전력_MW: float | None = None
    연계전압_kV: float | None = None
    연계변전소: str = ""
    연계점: str = ""
    이용개시일: str | None = None
    계통보강예정일: str = ""
    예정여부: str = ""
    변경사유: str = ""
    정보상태: str = "잠정"
    출처문서명: str = ""
    출처페이지: str = ""


def _to_year_month(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not 1 <= mm <= 12:
        return None
    return f"{2000 + yy:04d}-{mm:02d}"


def parse_kpx_contract(detail: dict) -> GridContract | None:
    """KPX 사업 상세면의 추진이력에서 계통계약 정보를 뽑는다."""
    contract = GridContract(
        출처문서명="KPX 발전소 건설사업 추진현황",
        출처페이지=str(detail.get("출처페이지", "")))
    found = False

    for entry in detail.get("추진이력", []):
        raw = entry.get("원문", "")
        m = CONTRACT_LINE_RE.search(raw)
        if not m:
            continue
        found = True
        kind = (m.group(1) or "").strip()
        value = m.group(2).strip()

        if PLANNED_RE.search(value):                 # 괄호 = 예정
            contract.예정여부 = "예정"
            contract.계약상태 = "협의 중"
            contract.최초체결일 = _to_year_month(value)
            contract.정보상태 = "예정"
            continue
        if value in NO_CONTRACT_MARKS or value.strip("-").strip() == "":
            contract.계약상태 = "미체결"             # '-' 는 날짜로 넣지 않는다
            continue

        date = _to_year_month(value)
        if date:
            if kind == "변경":
                contract.최근변경일 = date
                contract.계약상태 = "변경"
            else:
                contract.최초체결일 = date
                if contract.계약상태 not in ("변경",):
                    contract.계약상태 = "체결"

        voltage = VOLTAGE_RE.search(raw)
        if voltage:
            contract.연계전압_kV = float(voltage.group(1))
        substation = SUBSTATION_RE.search(raw)
        if substation:
            contract.연계변전소 = substation.group(1) + "변전소"

    return contract if found else None


def extract_grid_condition(agenda: dict) -> str:
    """회의록/개최결과의 '계통보강 후 연계가능' 류를 허가조건 문자열로 돌려준다.

    이는 계약 정보가 아니므로 GridContract 로 만들지 않고, 사업 대표정보의
    허가조건 칸에 넣을 문자열만 반환한다(지침 §14).
    """
    blob = " ".join(str(agenda.get(c, "")) for c in
                    ("사업준비기간", "변경사항", "기타필드", "안건명"))
    m = re.search(r"계통\s*보강[^,]*?연계\s*가능", blob)
    if m:
        return m.group(0).strip()
    m = re.search(r"한전\s*계통연계\s*가능\s*기한[^)]*", blob)
    if m:
        return m.group(0).strip()
    return ""


def to_records(contracts: list[GridContract]) -> list[dict]:
    return [asdict(c) for c in contracts]
