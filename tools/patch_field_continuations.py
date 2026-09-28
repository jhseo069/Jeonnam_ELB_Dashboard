r"""추출 CSV의 '줄바꿈으로 잘린 칸 값'을 원문에서 다시 읽어 보완한다.

회의록 표기 예:
    ㅇ 발전소 위치 : 전남 신안군 하의면 후광3리 산50번지 일원
                     → 전남 진도군 진도읍 산월리 산9번지 외 12필지
초기 추출기는 둘째 줄('→ …')을 버려 변경 후 위치가 사라졌다(제306차 장병도 등).
minutes 파서에 이어붙이기를 넣은 뒤, 기존 추출 CSV 전체를 새로 만들지 않고
'새 값이 옛 값으로 시작하면서 → 로 이어지는' 칸만 교체한다. 원문 대조로 확정한
OCR 보정값(보정메모)과 다른 칸은 그대로 둔다.

실행: .venv\Scripts\python.exe tools\patch_field_continuations.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extractors import minutes                                  # noqa: E402
from extractors.hwp_text import extract_text                    # noqa: E402

EXTRACTED = ROOT / "data/extracted"
FIELDS = ("발전소위치", "사업주체", "최대주주")


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def find_source(doc_name: str) -> Path | None:
    for path in (ROOT / "data/raw").rglob("*"):
        if path.is_file() and path.stem == doc_name:
            return path
    return None


def reextract(path: Path, doc_name: str, method: str) -> list:
    if method == "OCR" or method.startswith("OCR"):
        return minutes.extract_from_ocr(str(path), doc_name)
    if path.suffix.lower() in (".hwp", ".hwpx"):
        return minutes.extract_from_text(extract_text(path), doc_name)
    return minutes.extract(str(path), doc_name)


def main() -> None:
    total = 0
    for csv in sorted(EXTRACTED.glob("회의록_안건_*.csv")):
        df = pd.read_csv(csv, encoding="utf-8-sig")
        changed = 0
        for (doc, method), rows in df.groupby(["출처문서명", "추출방식"]):
            path = find_source(str(doc))
            if path is None:
                print(f"  원문 없음: {doc}")
                continue
            try:
                items = reextract(path, str(doc), str(method))
            except Exception as exc:                       # 문서 1건 실패는 건너뜀
                print(f"  재추출 실패 {doc}: {exc}")
                continue
            fresh = {_norm(i.안건명): i for i in items}
            for idx in rows.index:
                item = fresh.get(_norm(df.at[idx, "안건명"]))
                if item is None:
                    continue
                for field in FIELDS:
                    old, new = df.at[idx, field], getattr(item, field)
                    if pd.isna(old) or not new:
                        continue
                    if (len(_norm(new)) > len(_norm(old)) and "→" in str(new)
                            and _norm(new).startswith(_norm(old))):
                        df.at[idx, field] = new
                        changed += 1
                        print(f"  [{csv.stem[-4:]}] {df.at[idx, '회차']}차 "
                              f"{str(df.at[idx, '안건명'])[:26]} | {field}: … {str(new)[len(str(old)):][:60]}")
        if changed:
            df.to_csv(csv, index=False, encoding="utf-8-sig")
        print(f"{csv.name}: {changed}칸 보완")
        total += changed
    print(f"총 {total}칸 보완")


if __name__ == "__main__":
    main()
