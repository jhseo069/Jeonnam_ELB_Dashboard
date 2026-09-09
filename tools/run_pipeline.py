r"""추출 결과 -> 발전소 통합목록 파이프라인.

실행: .venv\Scripts\python.exe tools\run_pipeline.py

입력 (data/extracted)
  회의록_안건_전체.csv   HWP 본문 직접 파싱
  회의록_안건_PDF.csv    PDF 텍스트 파싱
  회의록_안건_OCR.csv    Windows OCR (한글이 이미지로 박힌 회의록)
  허가대장_전남광주_대상.csv
출력 (data/processed, data/interim)
  발전소_통합목록.csv, 허가이력.csv, 검수필요_*.csv

같은 입력이면 같은 결과가 나오도록 구성한다(지침 §19). 사용자가 원문을 대조해
OCR 값을 고친 뒤 다시 돌려도 그대로 반영된다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extractors.grid_contract import (extract_grid_condition,             # noqa: E402
                                      parse_kpx_contract, to_records as grid_records)
from transforms.dedup import apply_merges, find_duplicate_groups, name_core  # noqa: E402
from transforms.enrich import enrich_with_ledger                          # noqa: E402
from transforms.merge import build_projects                               # noqa: E402
from transforms.normalize import (classify_region, normalize_capacity,    # noqa: E402
                                  normalize_company, normalize_date,
                                  normalize_energy_source)
from transforms.wind_type import resolve as resolve_wind                  # noqa: E402

TARGET_SOURCES = ("태양광", "육상풍력", "해상풍력", "풍력_구분미상", "복합_검수필요")
REGION_PATTERN = "전남|전라남도|광주|전남광주통합특별시"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, encoding="utf-8-sig")
    return frame.where(pd.notna(frame), None)


def load_agenda() -> pd.DataFrame:
    parts = [read_csv(ROOT / "data/extracted" / name) for name in
             ("회의록_안건_전체.csv", "회의록_안건_PDF.csv", "회의록_안건_OCR.csv")]
    agenda = pd.concat([p for p in parts if len(p)], ignore_index=True)

    # 같은 회차·안건을 여러 경로로 뽑은 경우 텍스트 추출을 우선한다.
    # OCR 은 지침 §22에 따라 확정 데이터로 쓰지 않으므로 마지막 순위다.
    priority = {"HWP본문": 0, "텍스트": 1, "OCR+원문확정": 0, "OCR": 3}
    agenda["_우선순위"] = agenda["추출방식"].map(priority).fillna(3)
    agenda = (agenda.sort_values("_우선순위")
                    .drop_duplicates(subset=["회차", "안건명"], keep="first")
                    .drop(columns="_우선순위")
                    .reset_index(drop=True))
    return agenda


def normalize_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    """허가대장 원본 추출값에 표준화를 적용한다."""
    records, previous_date = [], None
    for row in ledger.to_dict("records"):
        source, source_state = normalize_energy_source(row.get("원동력") or "")
        date, date_state = normalize_date(str(row.get("허가변경일") or ""), previous_date)
        if date and date_state != "확인필요":
            previous_date = date
        location = f"{row.get('위치') or ''} {row.get('위치_추가행') or ''}".strip()
        region, sigungu, region_state = classify_region(location)
        capacity, capacity_state = normalize_capacity(str(row.get("용량_원문") or ""))
        records.append({**row,
                        "발전원_표준": source, "발전원_상태": source_state,
                        "허가변경일_표준": date, "날짜_상태": date_state,
                        "지역": region, "시군구": sigungu, "지역_상태": region_state,
                        "설비용량_MW_표준": capacity, "용량_상태": capacity_state,
                        "사업자_표준": normalize_company(row.get("사업자") or "")})
    return pd.DataFrame(records)


def build_permit_history(agenda: pd.DataFrame, projects: pd.DataFrame) -> pd.DataFrame:
    """안건 1건 = 이력 1행. 사업 대표정보와 분리해 보관한다(지침 §9)."""
    from transforms.merge import canonical_project_name, matching_key

    lookup: dict[tuple[str, str], str] = {}
    for project in projects.to_dict("records"):
        for alias in str(project.get("발전소명_원문") or "").split(" | "):
            if alias:
                lookup[(matching_key(alias), project.get("시군구") or "")] = project["프로젝트ID"]

    rows = []
    for record in agenda.to_dict("records"):
        canonical = canonical_project_name(record.get("안건명") or "")
        _, sigungu, _ = classify_region(record.get("발전소위치") or "")
        rows.append({
            "프로젝트ID": lookup.get((matching_key(canonical), sigungu), ""),
            "전기위원회회차": record.get("회차"),
            "안건명": record.get("안건명"),
            "안건유형": record.get("안건유형"),
            "사업주체": record.get("사업주체"),
            "최대주주": record.get("최대주주"),
            "양도인": record.get("양도인"),
            "양수인": record.get("양수인"),
            "변경사항": record.get("변경사항"),
            "설비용량_MW": record.get("설비용량_MW"),
            "총사업비_억원": record.get("총사업비_억원"),
            "사업준비기간": record.get("사업준비기간"),
            "출처문서명": record.get("출처문서명"),
            "출처페이지": record.get("출처페이지"),
            "추출방식": record.get("추출방식"),
            "데이터상태": ("검수필요" if record.get("추출방식") == "OCR"
                        else "확정" if record.get("추출방식") == "OCR+원문확정"
                        else "잠정"),
        })
    return pd.DataFrame(rows)


def build_grid_contracts(projects: pd.DataFrame) -> pd.DataFrame:
    """KPX 상세면에서 송배전이용계약 정보를 뽑아 프로젝트에 매칭한다.

    KPX 발전소명은 회의록 안건명과 표기가 달라 별칭 키가 안 맞으므로, 시군구가
    같은 후보 안에서 사업명 고유부 유사도로 잇는다. 못 붙는 계약은 KPX 에만
    존재하는 사업(최근 5년 심의 없음 또는 대상외 발전원)이므로 버리지 않고
    프로젝트ID 빈 채로 남긴다.
    """
    from rapidfuzz import fuzz

    detail = json.loads((ROOT / "data/extracted/kpx_2026H1.json")
                        .read_text(encoding="utf-8"))["상세"]
    jeonnam = [d for d in detail
               if "전라남도" in d.get("위치_원문", "") or "광주" in d.get("위치_원문", "")]

    def match(name: str, sigungu: str) -> str:
        core, best, best_score = name_core(name), "", 0
        for project in projects.to_dict("records"):
            if project.get("시군구") != sigungu:
                continue
            for alias in str(project.get("발전소명_원문") or "").split(" | "):
                score = fuzz.ratio(core, name_core(alias))
                if score > best_score:
                    best, best_score = project["프로젝트ID"], score
        return best if best_score >= 78 else ""

    contracts = []
    for record in jeonnam:
        contract = parse_kpx_contract(record)
        if not contract:
            continue
        _, sigungu, _ = classify_region(record.get("위치_원문", ""))
        contract.프로젝트ID = match(record.get("발전소명", ""), sigungu)
        contracts.append(contract)
    return pd.DataFrame(grid_records(contracts))


def main() -> None:
    agenda = load_agenda()
    print(f"[1] 회의록 안건 통합 {len(agenda)}건 "
          f"({agenda['추출방식'].value_counts().to_dict()})")

    target = agenda[agenda["발전소위치"].fillna("").str.contains(REGION_PATTERN, na=False)]
    (ROOT / "data/interim").mkdir(parents=True, exist_ok=True)
    target.to_csv(ROOT / "data/interim/회의록_전남광주_통합.csv",
                  index=False, encoding="utf-8-sig")
    print(f"[2] 전남·광주 안건 {len(target)}건")

    projects = pd.DataFrame([asdict(p) for p in
                             build_projects(target.to_dict("records")).values()])
    projects["발전소명_원문"] = projects["발전소명_원문"].apply(lambda x: " | ".join(x))
    projects = projects[projects["발전원"].isin(TARGET_SOURCES)]

    # 지역 재확인: '경기도 광주시'처럼 타 광역이 '광주' 문자에 걸려든 건을 뺀다
    before = len(projects)
    projects = projects[projects["지역"].isin(["전남", "광주"])]
    dropped = before - len(projects)
    print(f"[3] 사업 단위 {len(projects)}건 (대상 발전원, 타지역 {dropped}건 제외)")

    ledger = normalize_ledger(read_csv(ROOT / "data/extracted/허가대장_전남광주_대상.csv"))
    ledger.to_csv(ROOT / "data/interim/허가대장_전남광주_정규화.csv",
                  index=False, encoding="utf-8-sig")
    enriched = pd.DataFrame(enrich_with_ledger(projects.to_dict("records"),
                                               ledger.to_dict("records")))
    print(f"[4] 허가대장 병합 — 최초허가일 {enriched['최초발전사업허가일'].notna().sum()}건")

    kpx = read_csv(ROOT / "data/interim/kpx_2026H1_전남광주_대상.csv").to_dict("records")
    records = []
    for record in enriched.to_dict("records"):
        source, basis = resolve_wind(record, kpx)
        record["발전원"], record["발전원_근거"] = source, basis or record.get("발전원_근거", "")
        records.append(record)
    resolved = pd.DataFrame(records)
    print(f"[5] 발전원 확정 — 풍력 구분미상 {(resolved['발전원']=='풍력_구분미상').sum()}건 잔여")

    groups, suspects = find_duplicate_groups(resolved.to_dict("records"))
    merged = pd.DataFrame(apply_merges(resolved.to_dict("records"), groups))
    for column in ("설비용량_MW", "총사업비_억원"):
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    print(f"[6] 중복 정리 — {len(resolved)} → {len(merged)}건 "
          f"(자동병합 {sum(len(v) for v in groups.values())}건, 중복의심 {len(suspects)}건)")

    # 허가대장에만 있는 사업 편입 (2021년 이전 최초허가 등, 출처 구분해 표시)
    from transforms.ledger_only import build_ledger_only_projects
    ledger_only = build_ledger_only_projects(merged.to_dict("records"),
                                             ledger.to_dict("records"))
    if ledger_only:
        merged["편입경로"] = merged.get("편입경로", "회의록")
        merged = pd.concat([merged, pd.DataFrame(ledger_only)], ignore_index=True)
        for column in ("설비용량_MW", "총사업비_억원"):
            merged[column] = pd.to_numeric(merged[column], errors="coerce")
    print(f"[6b] 허가대장 전용 사업 편입 {len(ledger_only)}건 → 총 {len(merged)}건")

    # 허가조건(계통보강 조건)을 사업 대표정보에 반영 — 계약 정보가 아니다(지침 §14)
    conditions: dict[str, str] = {}
    for record in target.to_dict("records"):
        condition = extract_grid_condition(record)
        if condition:
            from transforms.merge import canonical_project_name, matching_key
            _, sigungu, _ = classify_region(record.get("발전소위치") or "")
            key = matching_key(canonical_project_name(record.get("안건명") or ""))
            conditions.setdefault((key, sigungu), condition)
    merged["계통보강조건"] = ""
    from transforms.merge import matching_key as _mk
    for idx, row in merged.iterrows():
        for alias in str(row.get("발전소명_원문") or "").split(" | "):
            hit = conditions.get((_mk(alias), row.get("시군구")))
            if hit:
                merged.at[idx, "계통보강조건"] = hit
                break

    # 확정 보정: 사용자 허가증(안좌쏠라시티 1·2호), 타지역 오귀속 제외(봉진·경주)
    from transforms.overrides import apply_overrides
    merged, removed = apply_overrides(merged)
    if removed:
        pd.DataFrame(removed).to_csv(ROOT / "data/interim/제외_타지역_위치오귀속.csv",
                                     index=False, encoding="utf-8-sig")
    print(f"[6c] 확정 보정 적용 — 제외/병합 {len(removed)}건 → 총 {len(merged)}건")

    (ROOT / "data/processed").mkdir(parents=True, exist_ok=True)
    merged.to_csv(ROOT / "data/processed/발전소_통합목록.csv",
                  index=False, encoding="utf-8-sig")
    history = build_permit_history(agenda, merged)
    history.to_csv(ROOT / "data/processed/허가이력.csv", index=False, encoding="utf-8-sig")

    contracts = build_grid_contracts(merged)
    contracts.to_csv(ROOT / "data/processed/계통계약.csv", index=False, encoding="utf-8-sig")
    # 중복의심은 overrides 이전(자동병합 단계) 기준이다. overrides 가 병합/재분류로
    # 처리한 그룹은 자신 또는 중복후보 중 하나가 최종 master 에서 사라지므로, 그런
    # 건은 해소된 것으로 보고 검수에서 제외한다. 원래 그룹이 그대로 남은(아무것도
    # 병합 안 된) 충돌만 진짜 미해소 검수 대상이다.
    surviving_ids = set(merged["프로젝트ID"])
    id_to_name = dict(zip(merged["프로젝트ID"], merged["발전소명"]))

    # 원문 대조로 '별개 사업'임이 확정되어 검수에서 내린 위치 충돌 그룹.
    # (발전소명에 포함되면 확정 처리) 완도 금일 1단계(200MW)·2단계(400MW)는
    # 제325차 회의록에 각각 별도 변경허가로 올라온 별개 사업이다.
    CONFIRMED_SEPARATE = ("금일",)

    def is_open(s: dict) -> bool:
        if s.get("프로젝트ID") not in surviving_ids:
            return False   # 자신이 병합/제외됨
        partners = [p for p in str(s.get("중복후보", "")).split(" / ") if p]
        if partners and any(p not in surviving_ids for p in partners):
            return False   # 짝이 병합/제외됨 → 이 건은 이제 단독
        name = id_to_name.get(s.get("프로젝트ID"), "")
        if any(tok in name for tok in CONFIRMED_SEPARATE):
            return False   # 원문 대조로 별개 사업 확정
        return True

    open_suspects = [s for s in suspects if is_open(s)]
    review_path = ROOT / "data/interim/검수필요_중복의심.csv"
    if open_suspects:
        pd.DataFrame(open_suspects).to_csv(review_path, index=False, encoding="utf-8-sig")
    elif review_path.exists():
        review_path.unlink()   # 전건 해소되면 stale 파일 제거
    print(f"[7] 저장 완료 — 발전소 {len(merged)}건 / 허가이력 {len(history)}행 "
          f"/ 계통계약 {len(contracts)}건 (체결 {(contracts['계약상태']=='체결').sum()}건)")

    summary = merged.groupby(["지역", "발전원"]).agg(
        사업수=("프로젝트ID", "size"), 총용량_MW=("설비용량_MW", "sum"),
        총사업비_억원=("총사업비_억원", "sum")).round(1)
    print("\n" + summary.to_string())
    print(f"\n총 설비용량 {merged['설비용량_MW'].sum():,.1f} MW")
    print(f"최초허가일 확보 {merged['최초발전사업허가일'].notna().sum()}건")
    print(f"최대주주 확보 {(merged['최대주주'].fillna('') != '').sum()}건")


if __name__ == "__main__":
    main()
