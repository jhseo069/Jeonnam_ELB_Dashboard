r"""원천 데이터를 Google Sheets 에 업로드 (지침 §8).

실행: .venv\Scripts\python.exe tools\upload_to_sheets.py

- .env 의 서비스계정/스프레드시트ID 사용 (키 원문은 코드·로그에 남기지 않음).
- 각 시트를 생성/갱신한다. 기존 동명 시트는 '_bak_<이름>_MMDD' 로 보존 후 교체하며,
  이전 백업은 지워 직전 1벌만 남긴다(업로드마다 백업이 쌓이지 않게).
- 첫 행 고정, 헤더 서식, 숫자/날짜 형식 통일.
- 실패해도 XLSX/CSV 는 이미 만들어져 있으므로 수동 업로드가 가능하다.
- `--clean-backups`: 모든 _bak_* 백업 시트를 삭제하고 종료(일회성 정리).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import gspread
import pandas as pd
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))

import os                                                       # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]
CSV_DIR = ROOT / "data/processed/sheets_csv"
SHEET_ORDER = ["태양광", "육상풍력", "해상풍력", "발전소통합목록", "허가이력",
               "계통계약", "출처목록", "검수필요", "데이터사전", "변경이력"]


def open_spreadsheet():
    key_path = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON").strip())
    if not key_path.is_absolute():
        key_path = ROOT / key_path
    creds = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID").strip())


def upload_frame(spreadsheet, name: str, frame: pd.DataFrame) -> None:
    frame = frame.fillna("").astype(str)
    rows, cols = frame.shape
    try:
        existing = spreadsheet.worksheet(name)
        # 기존 시트는 백업으로 이름 변경 후 새로 만든다(덮어쓰기 전 버전 보존, 지침 §19).
        # 이전 백업(_bak_<이름>_* 날짜 불문)은 모두 지워 '직전 1벌'만 남긴다.
        # (안 그러면 업로드한 날짜마다 백업이 쌓여 시트가 수십 개가 된다)
        prefix = f"_bak_{name}_"
        for ws in spreadsheet.worksheets():
            if ws.title.startswith(prefix):
                spreadsheet.del_worksheet(ws)
        existing.update_title(f"_bak_{name}_{date.today():%m%d}")
    except gspread.WorksheetNotFound:
        pass

    ws = spreadsheet.add_worksheet(title=name, rows=max(rows + 5, 10),
                                   cols=max(cols + 2, 5))
    ws.update([frame.columns.tolist()] + frame.values.tolist(),
              value_input_option="RAW")
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True},
                      "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
                      "horizontalAlignment": "CENTER"})
    print(f"  업로드 {name:14s} {rows}행 x {cols}열", flush=True)


def clean_backups(spreadsheet) -> None:
    """모든 _bak_* 백업 시트를 삭제한다(일회성 정리용)."""
    removed = 0
    for ws in spreadsheet.worksheets():
        if ws.title.startswith("_bak_"):
            spreadsheet.del_worksheet(ws)
            print(f"  삭제 {ws.title}", flush=True)
            removed += 1
    print(f"\n백업 시트 {removed}개 삭제 완료")


def main() -> None:
    spreadsheet = open_spreadsheet()
    print(f"대상: {spreadsheet.title}\n{spreadsheet.url}\n")

    if "--clean-backups" in sys.argv:
        clean_backups(spreadsheet)
        return

    for name in SHEET_ORDER:
        csv = CSV_DIR / f"{name}.csv"
        if not csv.exists():
            print(f"  건너뜀 {name} (CSV 없음)")
            continue
        upload_frame(spreadsheet, name, pd.read_csv(csv, encoding="utf-8-sig"))

    # 초기 'Sheet1'/'시트1' 정리
    for junk in ("Sheet1", "시트1"):
        try:
            spreadsheet.del_worksheet(spreadsheet.worksheet(junk))
        except gspread.WorksheetNotFound:
            pass

    print(f"\n완료: {spreadsheet.url}")


if __name__ == "__main__":
    main()
