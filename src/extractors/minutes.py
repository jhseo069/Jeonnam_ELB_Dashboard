"""전기위원회 회의록 추출기.

회의록은 A4 가로 용지에 2쪽을 나란히 앉힌 2단 편집이다. 페이지 전체를 한 번에
읽으면 좌우 단의 문장이 번갈아 섞여 나오므로(지침 §7.2가 경고하는 행·열 혼선),
반드시 좌우를 나눠 읽은 뒤 이어 붙인다.

안건 블록 형태:
    7. 동양에너지 신안태양광 발전사업 주식취득(안)
    ㅇ 사업주체: (주)동양에너지
    ㅇ 최대주주: 이지이앤씨(주), 100%
    ㅇ 발전소 위치: 전라남도 신안군 지도읍 태천리 1525 일원
    ㅇ 설비용량: 22.5MW
    ㅇ 총사업비: 1,957억원
    ㅇ 사업준비기간: 현재 가동 중
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

import pdfplumber

AGENDA_HEAD_RE = re.compile(r"^\s*(\d{1,2})\.\s*(.+?\(안\))\s*$")
FIELD_RE = re.compile(r"^\s*[ㅇoO○●·▫0]?\s*([^:：]{2,14})\s*[:：]\s*(.*)$")

FIELD_ALIASES = {
    "사업주체": "사업주체",
    "최대주주": "최대주주",
    "발전소 위치": "발전소위치", "발전소위치": "발전소위치", "위치": "발전소위치",
    "설비용량": "설비용량", "용량": "설비용량",
    "총사업비": "총사업비", "사업비": "총사업비",
    "사업준비기간": "사업준비기간", "준비기간": "사업준비기간",
    "양도인": "양도인", "양수인": "양수인",
    "변경사항": "변경사항", "변경내용": "변경사항",
}

# 안건 제목에서 안건유형을 읽는다. 순서가 곧 우선순위다.
AGENDA_TYPE_RULES = [
    ("양수인가", ("양수인가", "양수 인가")),
    ("주식취득인가", ("주식취득", "주식 취득")),
    ("변경허가", ("변경허가", "변경 허가")),
    ("준비기간연장", ("준비기간", "준비 기간")),
    ("공사계획인가기간연장", ("공사계획인가기간", "공사계획 인가기간")),
    ("허가취소", ("허가취소", "허가 취소")),
    ("과징금", ("과징금",)),
    ("신규허가", ("발전사업 허가", "허가(안)")),
]

CAPACITY_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(MW|㎿|kW|㎾)", re.IGNORECASE)
COST_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*억\s*원")


@dataclass
class AgendaItem:
    회차: int | None = None
    안건번호: str = ""
    안건명: str = ""
    안건유형: str = "확인필요"
    사업주체: str = ""
    최대주주: str = ""
    발전소위치: str = ""
    설비용량_원문: str = ""
    설비용량_MW: float | None = None
    총사업비_원문: str = ""
    총사업비_억원: float | None = None
    사업준비기간: str = ""
    양도인: str = ""
    양수인: str = ""
    변경사항: str = ""
    출처문서명: str = ""
    출처페이지: int = 0
    추출방식: str = "텍스트"
    기타필드: dict = field(default_factory=dict)


def is_two_up_page(page) -> bool:
    """A4 가로 용지에 2쪽을 앉힌 배치인지 판별한다.

    가로가 세로보다 긴 페이지만 2단으로 본다. 세로 페이지를 반으로 자르면
    멀쩡한 한 줄이 두 동강 나므로 이 판별을 건너뛰면 안 된다.
    """
    return page.width > page.height * 1.15


def read_page_lines(page) -> list[tuple[str, int]]:
    """페이지에서 (줄, 구역번호)를 읽는다. 2쪽 배치면 좌·우를 나눠 읽는다."""
    lines: list[tuple[str, int]] = []
    if not is_two_up_page(page):
        for line in (page.extract_text() or "").splitlines():
            if line.strip():
                lines.append((line.rstrip(), 1))
        return lines

    mid = page.width / 2
    for column, box in enumerate(((0, 0, mid, page.height),
                                  (mid, 0, page.width, page.height)), 1):
        try:
            text = page.crop(box).extract_text() or ""
        except Exception:
            text = ""
        for line in text.splitlines():
            if line.strip():
                lines.append((line.rstrip(), column))
    return lines


def classify_agenda_type(title: str) -> str:
    compact = title.replace(" ", "")
    for label, tokens in AGENDA_TYPE_RULES:
        if any(token.replace(" ", "") in compact for token in tokens):
            return label
    return "확인필요"


def parse_capacity(raw: str) -> float | None:
    m = CAPACITY_RE.search(raw.replace(",", ""))
    if not m:
        return None
    value = float(m.group(1))
    return value / 1000 if m.group(2).lower() in ("kw", "㎾") else value


def parse_cost(raw: str) -> float | None:
    m = COST_RE.search(raw.replace(",", ""))
    return float(m.group(1)) if m else None


def parse_round(document_name: str) -> int | None:
    m = re.search(r"제\s*(\d{2,4})\s*차", document_name)
    return int(m.group(1)) if m else None


def _parse_lines(lines, document_name: str, round_no: int | None,
                 추출방식: str) -> list[AgendaItem]:
    """(줄, 구역번호, 페이지) 흐름에서 안건 블록을 뽑는다.

    구역번호가 바뀌면 진행 중이던 블록을 닫는다. PDF 2단 편집에서 좌우 단의
    필드가 한 안건으로 뒤섞이는 것을 막기 위한 장치다. 텍스트 입력은 구역이
    하나뿐이라 이 분기가 동작하지 않는다.
    """
    items: list[AgendaItem] = []
    current: AgendaItem | None = None
    last_section = None

    for line, section, page_no in lines:
        if last_section is not None and section != last_section:
            if current:
                items.append(current)
                current = None
        last_section = section

        head = AGENDA_HEAD_RE.match(line)
        if head:
            if current:
                items.append(current)
            title = head.group(2).strip()
            current = AgendaItem(
                회차=round_no, 안건번호=head.group(1), 안건명=title,
                안건유형=classify_agenda_type(title),
                출처문서명=document_name, 출처페이지=page_no, 추출방식=추출방식)
            continue

        if current is None:
            continue

        field_match = FIELD_RE.match(line)
        if not field_match:
            continue
        key = re.sub(r"\s+", " ", field_match.group(1)).strip()
        value = field_match.group(2).strip()
        mapped = FIELD_ALIASES.get(key) or FIELD_ALIASES.get(key.replace(" ", ""))
        if mapped == "설비용량":
            current.설비용량_원문 = value
            current.설비용량_MW = parse_capacity(value)
        elif mapped == "총사업비":
            current.총사업비_원문 = value
            current.총사업비_억원 = parse_cost(value)
        elif mapped:
            setattr(current, mapped, value)
        else:
            current.기타필드[key] = value

    if current:
        items.append(current)

    # 사업 정보가 하나도 없는 블록(규칙 개정안 등)은 버린다
    return [i for i in items
            if i.사업주체 or i.발전소위치 or i.설비용량_MW is not None]


def extract(path: str, document_name: str = "") -> list[AgendaItem]:
    """PDF 회의록에서 안건을 뽑는다(2단 편집 대응)."""
    document_name = document_name or path
    lines = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for line, column in read_page_lines(page):
                lines.append((line, (page_no, column), page_no))
    return _parse_lines(lines, document_name, parse_round(document_name), "텍스트")


def extract_from_text(text: str, document_name: str) -> list[AgendaItem]:
    """HWP 본문에서 직접 뽑은 텍스트로 안건을 파싱한다.

    HWP 스트림은 문서 논리 순서를 유지하므로 단 분리가 필요 없다. PDF 변환 경로에서
    생기던 좌우 단 혼입도, 이미지로 박힌 레이블 누락도 없다.
    """
    lines = [(line.rstrip(), 0, 0) for line in text.splitlines() if line.strip()]
    return _parse_lines(lines, document_name, parse_round(document_name), "HWP본문")


def to_records(items: list[AgendaItem]) -> list[dict]:
    return [asdict(i) for i in items]


def extract_from_ocr(path: str, document_name: str = "") -> list[AgendaItem]:
    """한글 본문이 이미지로 렌더링된 회의록을 Windows OCR 로 읽어 파싱한다.

    지침 §22에 따라 결과는 확정 데이터가 아니다. 추출방식을 'OCR' 로 남겨
    원문 대조 전까지 검수 대상임을 표시한다.
    """
    from .win_ocr import read_pdf_lines

    document_name = document_name or path
    lines = read_pdf_lines(path)
    return _parse_lines(lines, document_name, parse_round(document_name), "OCR")
