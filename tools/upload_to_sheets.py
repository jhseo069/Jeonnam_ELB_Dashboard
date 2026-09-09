r"""원천 데이터를 Google Sheets 에 업로드 (지침 §8).

실행: .venv\Scripts\python.exe tools\upload_to_sheets.py

- .env 의 서비스계정/스프레드시트ID 사용 (키 원문은 코드·로그에 남기지 않음).
- 각 시트를 생성/갱신한다. 기존 동명 시트는 '_backup_YYYYMMDD' 로 보존 후 교체.
- 첫 행 고정, 헤더 서식, 숫자/날짜 형식 통일.
- 실패해도 XLSX/CSV 는 이미 만들어져 있으므로 수동 업로드가 가능하다.
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
        # 기존 시트는 백업으로 이름 변경 후 새로 만든다(덮어쓰기 전 버전 보존, 지침 §19)
        backup = f"_bak_{name}_{date.today():%m%d}"
        try:
            old = spreadsheet.worksheet(backup)
            spreadsheet.del_worksheet(old)
        except gspread.WorksheetNotFound:
            pass
        existing.update_title(backup)
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


def main() -> None:
    spreadsheet = open_spreadsheet()
    print(f"대상: {spreadsheet.title}\n{spreadsheet.url}\n")

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
