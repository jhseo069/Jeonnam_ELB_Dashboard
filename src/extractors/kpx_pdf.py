"""KPX '발전소 건설사업 추진현황' PDF 추출기.

문서 구조 (2026년 상반기판 기준, 112쪽):
  - 총괄표      : 발전원별/지역별 집계
  - 요약표      : 구분 | 지역 | 발전소명 | 발전회사 | 용량(㎿) | 발전사업(변경)허가 | 준공(예정)
  - 사업 상세면 : 발전회사·발전원·위치·부지면적·용량(기수)·착공/준공·제작사/모델·현황
                  + '추진 현황' 이력표(최초허가 / N차변경 / 환경영향평가 등)

중요: 요약표의 `발전사업(변경)허가`는 문서 스스로 "발전사업 변경허가 취득시 가장 최근
      허가일을 사용함"이라고 명시한다. 이 값을 최초허가일로 사용하면 안 된다(지침 §22).
      최초허가일은 사업 상세면의 '최초허가' 행에서만 취한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import pdfplumber

SUMMARY_HEADER_KEYS = ("발전소명", "발전회사")
DETAIL_LABELS = ("발전회사", "위치", "용량", "제작사/모델")

# "'18. 08" / "‘18.08" / "18.08" → (2018, 8)
YEAR_MONTH_RE = re.compile(r"[''`‘’]?\s*(\d{2})\s*\.\s*(\d{1,2})")
# "77.4MW (4.3MW×18기)" → 총용량, 기당용량, 기수
CAPACITY_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*MW\s*(?:\(\s*([\d.]+)\s*MW\s*[×x*]\s*(\d+)\s*기\s*\))?",
    re.IGNORECASE)


def normalize_year_month(raw: str) -> str | None:
    """KPX 표기 두 자리 연도를 YYYY-MM 으로. 지침 §10.1(월까지만 확인) 준수."""
    if not raw:
        return None
    m = YEAR_MONTH_RE.search(raw)
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not 1 <= mm <= 12:
        return None
    return f"{2000 + yy:04d}-{mm:02d}"


def parse_capacity(raw: str) -> dict:
    """'77.4MW (4.3MW×18기)' → {'설비용량_MW': 77.4, '기당용량_MW': 4.3, '발전기수': 18}."""
    out = {"설비용량_MW": None, "기당용량_MW": None, "발전기수": None, "용량_원문": raw or ""}
    if not raw:
        return out
    m = CAPACITY_RE.search(raw.replace(",", ""))
    if m:
        out["설비용량_MW"] = float(m.group(1))
        if m.group(2):
            out["기당용량_MW"] = float(m.group(2))
        if m.group(3):
            out["발전기수"] = int(m.group(3))
    return out


@dataclass
class SummaryRow:
    발전원: str = ""
    지역: str = ""
    발전소명: str = ""
    발전회사: str = ""
    용량_원문: str = ""
    설비용량_MW: float | None = None
    최근허가_원문: str = ""
    최근허가_YYYYMM: str | None = None
    준공예정_원문: str = ""
    준공예정_YYYYMM: str | None = None
    출처페이지: int = 0


@dataclass
class DetailRecord:
    발전소명: str = ""
    발전회사: str = ""
    발전원: str = ""
    위치_원문: str = ""
    부지면적: str = ""
    용량_원문: str = ""
    설비용량_MW: float | None = None
    기당용량_MW: float | None = None
    발전기수: int | None = None
    착공준공_원문: str = ""
    제작사모델: str = ""
    현황: str = ""
    최초허가_YYYYMM: str | None = None
    최근변경_원문: str = ""
    최근변경_YYYYMM: str | None = None
    추진이력: list[dict] = field(default_factory=list)
    출처페이지: int = 0


def _cells(row) -> list[str]:
    return [re.sub(r"\s+", " ", str(c)).strip() if c else "" for c in row]


def extract_summary(pdf: pdfplumber.PDF) -> list[SummaryRow]:
    """요약표를 추출한다. 병합셀로 비어 있는 '구분'·'지역'은 직전 값을 이어받는다."""
    rows: list[SummaryRow] = []
    current_source = current_region = ""
    for page_no, page in enumerate(pdf.pages, 1):
        for table in page.extract_tables():
            if not table or not table[0]:
                continue
            header = _cells(table[0])
            if not all(any(k in h for h in header) for k in SUMMARY_HEADER_KEYS):
                continue
            for raw in table[1:]:
                c = _cells(raw)
                if len(c) < 7 or not c[2]:
                    continue
                current_source = c[0] or current_source
                current_region = c[1] or current_region
                cap = parse_capacity(c[4] + "MW" if c[4] and "MW" not in c[4] else c[4])
                rows.append(SummaryRow(
                    발전원=current_source, 지역=current_region,
                    발전소명=c[2], 발전회사=c[3],
                    용량_원문=c[4], 설비용량_MW=cap["설비용량_MW"],
                    최근허가_원문=c[5], 최근허가_YYYYMM=normalize_year_month(c[5]),
                    준공예정_원문=c[6], 준공예정_YYYYMM=normalize_year_month(c[6]),
                    출처페이지=page_no))
    return rows


def extract_details(pdf: pdfplumber.PDF) -> list[DetailRecord]:
    """사업 상세면을 추출한다. 페이지당 1개 사업을 전제로 한다."""
    records: list[DetailRecord] = []
    for page_no, page in enumerate(pdf.pages, 1):
        tables = page.extract_tables()
        if not tables:
            continue
        head = tables[0]
        flat = " ".join(" ".join(_cells(r)) for r in head[:6])
        if not all(label in flat for label in DETAIL_LABELS):
            continue

        text = page.extract_text() or ""
        rec = DetailRecord(출처페이지=page_no)
        rec.발전소명 = _cells(head[0])[0]

        # 좌측 라벨 - 우측 값 쌍을 텍스트에서 회수 (표 셀 병합이 불규칙해 텍스트가 안전)
        def grab(label: str, pattern: str) -> str:
            m = re.search(pattern, text)
            return m.group(1).strip() if m else ""

        rec.발전회사 = grab("발전회사", r"발전회사\s+(.+?)\s+발전원")
        rec.발전원 = grab("발전원", r"발전원\s+(\S+)")
        rec.위치_원문 = grab("위치", r"위치\s+(.+?)\s+부지면적")
        rec.부지면적 = grab("부지면적", r"부지면적\s+([\d,]+\s*㎡)")
        rec.용량_원문 = grab("용량", r"용량\s+(.+?)\s+착공/준공")
        rec.착공준공_원문 = grab("착공/준공", r"착공/준공\s+(.+?)(?:\n|제작사)")
        rec.제작사모델 = grab("제작사/모델", r"제작사/모델\s+(.+?)\s+현황")
        rec.현황 = grab("현황", r"현황\s+(\S+)")

        cap = parse_capacity(rec.용량_원문)
        rec.설비용량_MW = cap["설비용량_MW"]
        rec.기당용량_MW = cap["기당용량_MW"]
        rec.발전기수 = cap["발전기수"]

        # 추진 이력: '최초허가', 'N차변경' 행
        for table in tables:
            for raw in table:
                c = _cells(raw)
                joined = " ".join(c)
                if "최초허가" in joined:
                    rec.최초허가_YYYYMM = rec.최초허가_YYYYMM or normalize_year_month(joined)
                m = re.search(r"(\d+)\s*차\s*변경", joined)
                if m:
                    ym = normalize_year_month(joined)
                    if ym and (rec.최근변경_YYYYMM is None or ym > rec.최근변경_YYYYMM):
                        rec.최근변경_YYYYMM = ym
                        rec.최근변경_원문 = joined[:120]
                if c and any(c):
                    rec.추진이력.append({"원문": joined[:200]})
        records.append(rec)
    return records


def extract(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        return {
            "요약표": [asdict(r) for r in extract_summary(pdf)],
            "상세": [asdict(r) for r in extract_details(pdf)],
            "페이지수": len(pdf.pages),
        }
