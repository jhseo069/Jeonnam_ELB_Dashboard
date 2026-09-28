r"""전기위원회 게시글 목록 재수집 -> data/interim/korec_postings.csv 최신화.

두 게시판(위원회 개최결과 bbs_se=2, 공지사항 bbs_se=1) 전체 목록을 다시 긁는다.
download_korec_attachments.py 가 이 CSV를 입력으로 쓰므로, 새 회차를 받으려면
이 도구를 먼저 돌려야 한다. 기존 목록과 비교해 신규 게시물을 출력한다.

실행: .venv\Scripts\python.exe tools\scrape_korec_postings.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.collectors.korec import KorecCollector, to_records     # noqa: E402

OUT = ROOT / "data/interim/korec_postings.csv"
COLS = ["bbs_se", "bbs_label", "bbs_sntnc_no", "번호", "분류", "제목", "게시일",
        "조회수", "첨부파일명", "첨부경로", "게시글URL", "첨부파일URL", "회차"]


def main() -> None:
    collector = KorecCollector(delay_sec=1.0)
    posts = []
    for bbs in (2, 1):
        collector.warmup(bbs)
        got = collector.fetch_all_postings(bbs)
        print(f"bbs_se={bbs}: {len(got)}건")
        posts.extend(got)

    df = pd.DataFrame(to_records(posts))[COLS]
    old = pd.read_csv(OUT, encoding="utf-8-sig") if OUT.exists() else pd.DataFrame(columns=COLS)
    old_keys = set(zip(old["bbs_se"], old["bbs_sntnc_no"]))
    new = df[[(a, b) not in old_keys for a, b in zip(df["bbs_se"], df["bbs_sntnc_no"])]]
    print(f"\n총 {len(df)}건 (기존 {len(old)}건) / 신규 {len(new)}건")
    for _, r in new.sort_values("게시일").iterrows():
        has_file = bool(str(r["첨부파일명"]).strip()) and str(r["첨부파일명"]) != "nan"
        print(f"  신규: {r['게시일']} | {r['bbs_label']} | {r['제목']} | 첨부 {'O' if has_file else 'X'}")
    df.to_csv(OUT, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
