r"""대시보드 빌드: 템플릿에 VWorld 키/도메인을 주입하고 데이터를 JS 로 감싼다.

실행: .venv\Scripts\python.exe tools\build_dashboard.py

- API 키는 .env(VWORLD_API_KEY)에서 읽어 index.html 에만 채운다. index.html 은
  키가 들어 있으므로 .gitignore 로 커밋에서 제외한다(지침 §15: 키를 저장소에 두지 않음).
- data.json 을 data.json.js(전역 DATA 변수)로 감싸, 로컬 파일에서도 fetch 없이 로드된다.
- 서빙: .venv\Scripts\python.exe -m http.server 8080 --directory dashboard
  (등록 도메인이 localhost 일 때 Referer 를 맞추기 위함)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DASH = ROOT / "dashboard"


def main() -> None:
    key = (os.getenv("VWORLD_API_KEY") or "").strip()
    domain = (os.getenv("VWORLD_DOMAIN") or "localhost").strip()
    if not key:
        print("경고: VWORLD_API_KEY 가 비어 있습니다. 지도만 비활성화되고 표는 동작합니다.")

    template = (DASH / "index.template.html").read_text(encoding="utf-8")
    html = template.replace("__VWORLD_KEY__", key).replace("__VWORLD_DOMAIN__", domain)
    (DASH / "index.html").write_text(html, encoding="utf-8")

    data = json.loads((DASH / "data.json").read_text(encoding="utf-8"))
    (DASH / "data.json.js").write_text(
        "const DATA = " + json.dumps(data, ensure_ascii=False) + ";",
        encoding="utf-8")

    print(f"빌드 완료: {DASH / 'index.html'}")
    print(f"  VWorld 키: {'주입됨(' + str(len(key)) + '자)' if key else '없음'} / 도메인: {domain}")
    print(f"  데이터: {data['meta']['총사업수']}건")
    print("\n서빙:")
    print(r"  .venv\Scripts\python.exe -m http.server 8080 --directory dashboard")
    print("  브라우저에서 http://localhost:8080/ 접속")


if __name__ == "__main__":
    main()
