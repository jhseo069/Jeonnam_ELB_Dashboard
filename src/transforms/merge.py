"""세 출처(회의록·허가대장·KPX) 병합과 프로젝트ID 생성.

모집단은 전기위원회 자료(회의록 + 허가대장)로 잡는다. KPX 추진현황은 사업자가
자율 제출한 취합자료라 미제출 사업이 있을 수 있어 모집단이 될 수 없고, 전기위원회에
없는 항목(부지면적·발전기 기수·제작사·준공예정)을 채우는 보조 출처로만 쓴다.

최초허가일과 허가 변경이력은 전기위원회 자료를 정본으로 삼는다.

이름이 비슷하다는 이유만으로 병합하지 않는다(지침 §11). 유사도 임계값을 넘더라도
시군구가 다르면 별도 사업으로 두고, 애매하면 `중복의심`을 붙여 검수로 넘긴다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .normalize import EXCLUDED_TOKENS, classify_region, normalize_company

# 안건명 꼬리표: '~ 허가(안)', '~ 변경허가(안)' 등을 떼어 사업명만 남긴다
AGENDA_SUFFIX_RE = re.compile(
    r"\s*(?:발전사업\s*)?(?:신규|조건부)?\s*"
    r"(?:허가|변경허가|양수인가|양수\s*인가|주식취득\s*인가|주식취득|승인|인가|"
    r"과징금\s*부과|허가취소|취소)\s*\(안\)\s*$")
TRAILING_PAREN_RE = re.compile(r"\s*\(안\)\s*$")
# 사업명 앞뒤 군더더기
NOISE_RE = re.compile(r"[\s㈜()（）\[\]{}·,，.]+")

FUZZY_THRESHOLD = 88          # 이 값 미만이면 서로 다른 사업으로 본다
FUZZY_REVIEW_BAND = (80, 88)  # 이 구간은 '중복의심'으로 검수에 넘긴다


def canonical_project_name(agenda_title: str) -> str:
    """안건명에서 사업명만 남긴다. '영광 칠해1 해상풍력 발전사업 허가(안)' -> '영광 칠해1 해상풍력 발전사업'."""
    name = AGENDA_SUFFIX_RE.sub("", agenda_title or "")
    name = TRAILING_PAREN_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def matching_key(name: str) -> str:
    """중복 판정용 비교 키. 표기 흔들림만 없애고 서로 다른 이름을 합치지는 않는다."""
    return NOISE_RE.sub("", name or "").lower()


def make_project_id(canonical_name: str, sigungu: str) -> str:
    """자료가 갱신되어도 변하지 않는 내부 식별자(지침 §13).

    사업명과 시군구만으로 만든다. 사업주체·용량은 변경될 수 있어 넣지 않는다.
    """
    seed = f"{matching_key(canonical_name)}|{sigungu}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"JN-{digest}"


@dataclass
class Project:
    프로젝트ID: str = ""
    발전소명: str = ""
    발전소명_원문: list[str] = field(default_factory=list)
    발전원: str = "확인필요"
    발전원_근거: str = ""
    지역: str = "확인필요"
    시군구: str = ""
    사업주체: str = ""
    최대주주: str = ""
    허가위치_원문: str = ""
    설비용량_MW: float | None = None
    총사업비_억원: float | None = None
    최초발전사업허가일: str | None = None
    최초허가일_출처: str = ""
    최근전기위원회회차: int | None = None
    최근심의일: str | None = None
    최근안건유형: str = ""
    데이터상태: str = "잠정"
    중복의심: str = ""
    대표출처: str = ""
    출처페이지: str = ""
    이력건수: int = 0


# 사업명에 실제로 적힌 표기만 근거로 삼는다. 위치·용량으로 추정하지 않는다.
ENERGY_FROM_NAME = [
    ("해상풍력", ("해상풍력", "해상 풍력")),
    ("육상풍력", ("육상풍력", "육상 풍력")),
    ("태양광", ("태양광", "솔라", "쏠라", "solar", "햇빛")),
]


def infer_energy_source(names: list[str]) -> tuple[str, str]:
    """사업명 표기에서 발전원을 읽는다. 원문에 적힌 말만 근거로 삼는다.

    반환: (발전원, 근거문자열). 사업명에 없으면 ('확인필요', '') 로 두고
    다른 출처에서 채운다. 위치나 용량으로 추정하지 않는다(지침 §22).
    """
    joined = " ".join(names)
    # 제외 토큰에 'bess', 'srf' 처럼 영문이 있어 대소문자를 맞춰 비교한다
    compact = joined.replace(" ", "").lower()

    def excluded_token() -> str | None:
        return next((tok for tok in EXCLUDED_TOKENS if tok.lower() in compact), None)

    # 태양광 신호 중 '햇빛/솔라/쏠라'는 BESS·연료전지 사업의 브랜드명으로도 쓰인다
    # (예: '영광 햇빛 BESS'는 태양광이 아니라 BESS). 발전방식이 명확한 제외 토큰이
    # 있으면 브랜드성 태양광 표기보다 우선해 대상외로 판정한다.
    BRAND_LIKE_SOLAR = ("솔라", "쏠라", "solar", "햇빛")

    hit = excluded_token()
    if hit:
        # 명시적 태양광('태양광') 또는 명시적 풍력 표기가 함께 있을 때만 복합으로 본다
        explicit_solar = "태양광" in compact
        explicit_wind = "풍력" in compact
        if explicit_solar or explicit_wind:
            label = "태양광" if explicit_solar else "풍력"
            return "복합_검수필요", f"사업명에 '{label}'와 '{hit}' 동시 표기"
        return "대상외", f"사업명 표기: {hit} (브랜드성 태양광 표기 무시)"

    # 제외 토큰이 없을 때만 대상 발전원 판정
    for label, tokens in ENERGY_FROM_NAME:
        if any(token.replace(" ", "").lower() in compact for token in tokens):
            return label, f"사업명 표기: {label}"

    if "풍력" in compact:
        return "풍력_구분미상", "사업명에 '풍력'만 표기되어 육상/해상 미상"
    return "확인필요", ""


def build_projects(agenda_records: list[dict]) -> dict[str, Project]:
    """회의록 안건을 사업 단위로 묶는다. 안건 1건 = 이력 1건, 사업 1개 = 여러 이력."""
    projects: dict[str, Project] = {}
    index: list[tuple[str, str, str]] = []      # (비교키, 시군구, 프로젝트ID)

    for record in agenda_records:
        title = record.get("안건명", "")
        canonical = canonical_project_name(title)
        if not canonical:
            continue
        location = record.get("발전소위치", "")
        region, sigungu, _ = classify_region(location)
        key = matching_key(canonical)

        project_id = None
        review_note = ""
        for existing_key, existing_sigungu, existing_id in index:
            if existing_sigungu != sigungu:
                continue                       # 시군구가 다르면 합치지 않는다
            if existing_key == key:
                project_id = existing_id
                break
            score = fuzz.ratio(existing_key, key)
            if score >= FUZZY_THRESHOLD:
                project_id = existing_id
                break
            if FUZZY_REVIEW_BAND[0] <= score < FUZZY_REVIEW_BAND[1]:
                review_note = f"유사 사업명 {score:.0f}점: {existing_id}"

        if project_id is None:
            project_id = make_project_id(canonical, sigungu)
            index.append((key, sigungu, project_id))
            projects[project_id] = Project(
                프로젝트ID=project_id, 발전소명=canonical,
                지역=region, 시군구=sigungu, 중복의심=review_note)

        project = projects[project_id]
        if canonical not in project.발전소명_원문:
            project.발전소명_원문.append(canonical)
        project.이력건수 += 1

        # 대표값은 최신 회차 기준으로 갱신한다
        round_no = record.get("회차")
        round_no = int(round_no) if str(round_no).strip() not in ("", "nan", "None") else None
        is_newer = (round_no is not None and
                    (project.최근전기위원회회차 is None or round_no >= project.최근전기위원회회차))
        if is_newer:
            project.최근전기위원회회차 = round_no
            project.최근안건유형 = record.get("안건유형", "")
            for src_field, dst_field in (("사업주체", "사업주체"), ("최대주주", "최대주주"),
                                         ("발전소위치", "허가위치_원문")):
                value = (record.get(src_field) or "").strip()
                if value:
                    setattr(project, dst_field, value)
            for src_field, dst_field in (("설비용량_MW", "설비용량_MW"),
                                         ("총사업비_억원", "총사업비_억원")):
                value = record.get(src_field)
                if value not in (None, "", "nan"):
                    setattr(project, dst_field, float(value))
            project.대표출처 = record.get("출처문서명", "")
            project.출처페이지 = str(record.get("출처페이지", ""))

        if project.발전원 in ("확인필요", "풍력_구분미상"):
            source, basis = infer_energy_source(project.발전소명_원문)
            if source != "확인필요":
                project.발전원, project.발전원_근거 = source, basis

    return projects
