"""3MW 초과 발전사업 허가대장 PDF 추출기.

표 레이아웃이 두 종류 혼재한다(헤더 텍스트로 판별하므로 열 수에 의존하지 않는다).
  9열  : NO | 연번 | 상 호(사업자) | 발전소 위치 | 원동력 | 용량(MW) | *허가일*변경일 | 사업준비기간 | 기타(변경사항)
  10열 : 위 구성 + '공사계획 인가기간'

문서 자체 안내에 따른 판독 규칙:
  - `기타(변경사항)`가 **빈칸이면 그 행의 날짜가 최초 허가일**,
    내용이 있으면 그 변경 건에 대한 허가증 재교부일이다.
  - NO 칸이 비고 `발전소 위치`만 있는 행은 앞 행 위치의 이어진 줄이다.
    해상풍력은 여기에 사업구역 위경도·TM 좌표가 들어온다.

주의: 원동력은 `풍력`으로만 적혀 육상/해상이 구분되지 않는다.
      임의 판정하지 않고 `발전원_세부=확인필요`로 두고 다른 출처로 확정한다(지침 §22).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

import pdfplumber

HEADER_KEYS = ("상 호", "발전소 위치", "원동력")

COLUMN_ALIASES = {
    "상 호": "사업자", "상호": "사업자",
    "발전소 위치": "위치", "원동력": "원동력",
    "용량": "용량", "허가": "허가변경일", "허가일": "허가변경일",
    "공사계획": "공사계획인가기간", "사업": "사업준비기간", "기타": "기타변경사항",
}

# 위경도 DMS: 126°10'49.97"  /  E34°12'53.68"N
DMS_RE = re.compile(r"(\d{1,3})\s*°\s*(\d{1,2})\s*[′']\s*([\d.]+)\s*[″\"]")
# 도 단위 십진수: 126.4642
DECIMAL_RE = re.compile(r"\b(1[2-3]\d\.\d{3,})\b|\b(3[3-9]\.\d{3,})\b")

TARGET_MOTIVE = ("태양광", "풍력")
REGION_TOKENS = ("전남", "전라남도", "광주", "광주광역시", "전남광주통합특별시")


@dataclass
class LedgerRow:
    연도: str = ""
    연번: str = ""
    사업자: str = ""
    위치: str = ""
    위치_추가행: str = ""
    원동력: str = ""
    발전원_세부: str = "확인필요"
    용량_원문: str = ""
    설비용량_MW: float | None = None
    허가변경일: str = ""
    공사계획인가기간: str = ""
    사업준비기간: str = ""
    기타변경사항: str = ""
    이력구분: str = ""          # 최초허가 / 변경
    좌표_원문: str = ""
    출처페이지: int = 0


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value else ""


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        text = _clean(cell)
        for key, field in COLUMN_ALIASES.items():
            if key in text and field not in mapping:
                mapping[field] = idx
                break
    return mapping


def parse_capacity(raw: str) -> float | None:
    """'30.03' -> 30.03 / '45만kw×2' 같은 비정형은 None (검수 대상)."""
    text = raw.replace(",", "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def extract_coordinates(text: str) -> str:
    """위치 문자열에 섞인 좌표 표기를 원문 그대로 회수한다(변환은 하지 않음)."""
    parts = []
    if DMS_RE.search(text):
        parts.append(text)
    elif re.search(r"\d{6,7}\.\d+\s+\d{6,7}\.\d+", text):   # TM 좌표쌍
        parts.append(text)
    return " ".join(parts)


def is_coordinate_fragment(text: str) -> bool:
    return bool(DMS_RE.search(text) or re.search(r"\d{6,7}\.\d+\s+\d{6,7}\.\d+", text))


def extract(path: str, page_limit: int | None = None,
            on_page=None) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[:page_limit] if page_limit else pdf.pages
        for page_no, page in enumerate(pages, 1):
            for table in page.extract_tables():
                if not table or not table[0]:
                    continue
                header = [_clean(c) for c in table[0]]
                if not all(any(k in h for h in header) for k in HEADER_KEYS):
                    continue
                cols = _map_columns(header)
                if "위치" not in cols or "원동력" not in cols:
                    continue

                for raw in table[1:]:
                    cells = [_clean(c) for c in raw]
                    if not any(cells):
                        continue
                    get = lambda f: cells[cols[f]] if f in cols and cols[f] < len(cells) else ""

                    위치 = get("위치")
                    사업자 = get("사업자")
                    원동력 = get("원동력")

                    # 이어진 줄: NO/사업자/원동력이 모두 비고 위치만 있는 행
                    if not 사업자 and not 원동력 and 위치:
                        if rows:
                            rows[-1].위치_추가행 = (rows[-1].위치_추가행 + " " + 위치).strip()
                            if is_coordinate_fragment(위치):
                                rows[-1].좌표_원문 = (rows[-1].좌표_원문 + " " + 위치).strip()
                        continue

                    if not 위치 and not 사업자:
                        continue

                    기타 = get("기타변경사항")
                    용량_원문 = get("용량")
                    entry = LedgerRow(
                        연도=cells[0] if cells else "",
                        연번=cells[1] if len(cells) > 1 else "",
                        사업자=사업자, 위치=위치, 원동력=원동력,
                        용량_원문=용량_원문, 설비용량_MW=parse_capacity(용량_원문),
                        허가변경일=get("허가변경일"),
                        공사계획인가기간=get("공사계획인가기간"),
                        사업준비기간=get("사업준비기간"),
                        기타변경사항=기타,
                        이력구분="최초허가" if not 기타 else "변경",
                        좌표_원문=extract_coordinates(위치),
                        출처페이지=page_no)
                    rows.append(entry)
            if on_page:
                on_page(page_no, len(rows))
    return rows


def filter_target(rows: list[LedgerRow]) -> list[LedgerRow]:
    """전남·광주 + 태양광/풍력만 남긴다. 지역 판정은 위치 원문 토큰으로 한다."""
    out = []
    for row in rows:
        full_location = f"{row.위치} {row.위치_추가행}"
        if not any(token in full_location for token in REGION_TOKENS):
            continue
        if not any(m in row.원동력 for m in TARGET_MOTIVE):
            continue
        out.append(row)
    return out


def to_records(rows: list[LedgerRow]) -> list[dict]:
    return [asdict(r) for r in rows]
