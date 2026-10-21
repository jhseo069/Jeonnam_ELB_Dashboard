r"""새 회의록 PDF의 안건을 추출해 data/extracted/회의록_안건_OCR.csv 에 추가한다.

최근 회차 회의록 PDF는 글자 중복·표 뒤섞임 때문에 텍스트 파서가 실패하므로
Windows OCR 경로(minutes.extract_from_ocr)를 쓴다. 추출방식='OCR' 은 검수 대상이다.
같은 회차 행이 이미 있으면 지우고 다시 넣는다(재실행 안전).

출처문서명은 원문 파일명(확장자 제외)으로 기록한다 — patch_field_continuations.py 가
이 이름으로 원문을 다시 찾는다.

실행: .venv\Scripts\python.exe tools\add_minutes.py "<회의록 PDF 경로>"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extractors import minutes                                  # noqa: E402
from transforms.apply_ocr_corrections import apply_corrections   # noqa: E402

OCR_CSV = ROOT / "data/extracted/회의록_안건_OCR.csv"
RULES = ROOT / "data/interim/OCR_보정규칙.json"


def main(pdf: str) -> None:
    path = Path(pdf)
    round_no = minutes.parse_round(path.stem)
    if round_no is None:
        sys.exit(f"파일명에서 회차를 못 찾음: {path.name}")
    items = minutes.extract_from_ocr(str(path), path.stem)
    new = pd.DataFrame(minutes.to_records(items))
    if new.empty:
        sys.exit("추출된 안건 0건 — 개최결과(요약본)이거나 형식 변경. 원문 확인 필요")
    new["회차"] = round_no
    # 원문 대조로 확정한 보정(OCR_보정규칙.json)이 이 회차에 있으면 재적용
    rules = [r for r in json.loads(RULES.read_text(encoding="utf-8")) if r["회차"] == round_no] \
        if RULES.exists() else []
    if rules:
        tmp = ROOT / "data/interim/_rules_tmp.json"
        tmp.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        new = apply_corrections(new, tmp)
        tmp.unlink()
        print(f"원문 대조 보정 {len(rules)}건 적용")

    existing = pd.read_csv(OCR_CSV, encoding="utf-8-sig")
    for col in existing.columns:
        if col not in new.columns:
            new[col] = None
    existing = existing[pd.to_numeric(existing["회차"], errors="coerce") != round_no]
    merged = pd.concat([existing, new[existing.columns]], ignore_index=True)
    merged.to_csv(OCR_CSV, index=False, encoding="utf-8-sig")

    print(f"제{round_no}차 안건 {len(new)}건 추가 → {OCR_CSV.name} 총 {len(merged)}행")
    for _, r in new.iterrows():
        print(f"  {r['안건유형']:>6} | {r['안건명']} | {r['사업주체']} | "
              f"{r['설비용량_MW']}MW | {str(r['발전소위치'])[:40]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
