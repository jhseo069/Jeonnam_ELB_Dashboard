r"""전기위원회 첨부파일 일괄 다운로드 (최근 5년, 증분).

실행: .venv\Scripts\python.exe tools\download_korec_attachments.py [--from 2021-01-01]

- data/interim/korec_postings.csv 의 게시글 목록을 입력으로 쓴다.
- 이미 받은 파일은 건너뛴다(증분 수집, 지침 §19).
- 문서별 실패는 기록하고 전체 작업을 중단하지 않는다.
- 결과 메타데이터(파일해시 포함)는 data/raw/korec/_attachments_meta.json 에 누적한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from collectors.korec import KorecCollector, Posting  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTINGS_CSV = PROJECT_ROOT / "data/interim/korec_postings.csv"
RAW_DIR = PROJECT_ROOT / "data/raw/korec"
META_PATH = RAW_DIR / "_attachments_meta.json"
LOG_PATH = PROJECT_ROOT / "logs/korec_download.log"


def build_posting(row) -> Posting:
    return Posting(
        bbs_se=int(row["bbs_se"]), bbs_label=row["bbs_label"],
        bbs_sntnc_no=int(row["bbs_sntnc_no"]), 번호=str(row["번호"]),
        분류=str(row["분류"]), 제목=row["제목"], 게시일=row["게시일"],
        조회수=str(row["조회수"]), 첨부파일명=row["첨부파일명"],
        첨부경로=row["첨부경로"], 게시글URL=row["게시글URL"],
        첨부파일URL=row["첨부파일URL"],
        회차=int(row["회차"]) if str(row["회차"]).strip() not in ("", "nan") else None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", default="2021-01-01")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    df = pd.read_csv(POSTINGS_CSV, encoding="utf-8-sig").fillna("")
    df["게시일_dt"] = pd.to_datetime(df["게시일"], errors="coerce")
    target = df[(df["게시일_dt"] >= args.date_from) & (df["첨부파일명"] != "")]

    existing = json.loads(META_PATH.read_text(encoding="utf-8")) if META_PATH.exists() else []
    done_ids = {m["문서ID"] for m in existing}

    collector = KorecCollector(delay_sec=args.delay)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    metas, failures = list(existing), []

    print(f"대상 {len(target)}건 (이미 완료 {len(done_ids)}건)", flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        for i, (_, row) in enumerate(target.iterrows(), 1):
            posting = build_posting(row)
            doc_id = f"KOREC-{posting.bbs_se}-{posting.bbs_sntnc_no}"
            if doc_id in done_ids:
                continue
            sub = "meeting_result" if posting.bbs_se == 2 else "notice"
            try:
                meta = collector.download_attachment(posting, RAW_DIR / sub)
                if meta:
                    metas.append(meta)
                    done_ids.add(doc_id)
                    mark = "신규" if meta["신규다운로드"] else "기존"
                    print(f"  [{i:>3}/{len(target)}] {mark} {meta['확장자']:6s} "
                          f"{meta['파일크기']:>9,}B  {meta['문서제목'][:44]}", flush=True)
            except Exception as exc:
                failures.append({"문서ID": doc_id, "제목": posting.제목, "사유": str(exc)})
                log.write(f"{doc_id}\t{posting.제목}\t{exc}\n{traceback.format_exc()}\n")
                print(f"  [{i:>3}/{len(target)}] 실패 {posting.제목[:40]} — {exc}", flush=True)

    META_PATH.write_text(json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        (RAW_DIR / "_download_failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n완료: 성공 {len(metas)}건 / 실패 {len(failures)}건")
    print(f"메타: {META_PATH}")


if __name__ == "__main__":
    main()
