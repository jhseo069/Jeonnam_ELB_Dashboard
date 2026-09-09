r"""Google Sheets 연결 점검 스크립트 (읽기 전용, 시트를 변경하지 않음).

실행:  .venv\Scripts\python.exe tools\check_google_sheets.py

.env 의 GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEETS_SPREADSHEET_ID 를 읽어
  1) 키 파일 존재와 형식
  2) 서비스 계정 이메일(= 시트에 공유해야 할 주소)
  3) 스프레드시트 접근 권한과 시트 목록
을 순서대로 확인한다. private_key 등 비밀값은 출력하지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def fail(message: str, hint: str = "") -> None:
    print(f"  [실패] {message}")
    if hint:
        print(f"         → {hint}")
    sys.exit(1)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    print("[1/3] 서비스 계정 키 파일 확인")
    raw_path = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw_path:
        fail("GOOGLE_SERVICE_ACCOUNT_JSON 이 비어 있습니다.",
             ".env 에 JSON 파일 경로를 입력하십시오.")

    key_path = Path(raw_path)
    if not key_path.is_absolute():
        key_path = PROJECT_ROOT / key_path
    if not key_path.exists():
        fail(f"키 파일을 찾을 수 없습니다: {key_path}",
             "Google Cloud Console > 서비스 계정 > 키 탭에서 JSON 키를 발급받아 두십시오.")

    try:
        info = json.loads(key_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"JSON 파싱 실패: {exc}", "다운로드한 원본 JSON 파일이 맞는지 확인하십시오.")

    if info.get("type") != "service_account":
        fail(f"서비스 계정 키가 아닙니다 (type={info.get('type')!r}).",
             "OAuth 클라이언트 JSON 이 아니라 '서비스 계정' 키를 받아야 합니다.")

    client_email = info.get("client_email", "")
    print(f"  [OK] 키 파일        : {key_path}")
    print(f"  [OK] 프로젝트 ID    : {info.get('project_id')}")
    print(f"  [OK] 서비스 계정    : {client_email}")
    print(f"       ↑ 이 주소를 스프레드시트 '공유 > 편집자' 로 추가해야 합니다.")

    print("\n[2/3] 인증")
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
        client = gspread.authorize(creds)
    except Exception as exc:
        fail(f"인증 실패: {type(exc).__name__}: {exc}",
             "Google Sheets API 와 Google Drive API 가 모두 사용 설정되었는지 확인하십시오.")
    print("  [OK] 인증 성공")

    print("\n[3/3] 스프레드시트 접근")
    sheet_id = (os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or "").strip()
    if not sheet_id:
        fail("GOOGLE_SHEETS_SPREADSHEET_ID 가 비어 있습니다.",
             "시트 URL 의 /d/ 와 /edit 사이 문자열을 입력하십시오.")
    if "/" in sheet_id:
        fail("URL 전체가 아니라 ID 부분만 넣어야 합니다.",
             "https://docs.google.com/spreadsheets/d/<이부분>/edit")

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}",
             f"시트를 {client_email} 에게 편집자로 공유했는지, ID 가 맞는지 확인하십시오.")

    print(f"  [OK] 문서명         : {spreadsheet.title}")
    print(f"  [OK] URL            : {spreadsheet.url}")
    worksheets = spreadsheet.worksheets()
    print(f"  [OK] 시트 {len(worksheets)}개       : {', '.join(ws.title for ws in worksheets)}")

    required = ["태양광", "육상풍력", "해상풍력", "발전소통합목록", "허가이력",
                "계통계약", "출처목록", "검수필요", "데이터사전", "변경이력"]
    missing = [name for name in required if name not in {ws.title for ws in worksheets}]
    if missing:
        print(f"\n  [안내] 아직 없는 시트 {len(missing)}개: {', '.join(missing)}")
        print("         업로더 실행 시 자동 생성됩니다.")

    print("\n점검 완료. 쓰기 권한 테스트는 실제 업로드 단계에서 수행합니다.")


if __name__ == "__main__":
    main()
