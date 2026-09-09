# 전남·광주 발전사업 데이터 수집·가공·대시보드

전기위원회·전력거래소(KPX) 공식 자료에서 전남·광주의 태양광·육상풍력·해상풍력
발전사업을 수집·정제·검증하여, 출처가 추적 가능한 원천 데이터(Google Sheets)와
VWorld 지도 기반 대시보드를 구축한다.

## 1. 환경 준비

```bash
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.env` 작성 (`.env.example` 복사):

```
VWORLD_API_KEY=발급키
VWORLD_DOMAIN=http://localhost:8080/     # 인증키 발급 시 등록한 URL
GOOGLE_SERVICE_ACCOUNT_JSON=secrets/서비스계정.json
GOOGLE_SHEETS_SPREADSHEET_ID=시트ID
```

키·인증정보는 코드/시트/로그/저장소에 두지 않는다. `.env`, `secrets/`,
`dashboard/index.html`(키 주입본)은 `.gitignore` 처리되어 있다.

## 2. 실행 순서

```bash
# (1) 게시글 목록 수집 + 첨부 다운로드 (증분, 해시 기록)
.venv\Scripts\python.exe tools\download_korec_attachments.py --from 2021-01-01

# (2) HWP → 텍스트 추출은 파이프라인에서 자동 (win_ocr 포함)
#     이미지로 박힌 회의록은 Windows 내장 OCR 사용 (Tesseract 불필요)

# (3) 추출 → 정규화 → 병합 → 통합목록 생성
.venv\Scripts\python.exe tools\run_pipeline.py

# (4) 좌표 확정 (해상=사업구역 좌표, 육상=VWorld 지오코딩)
.venv\Scripts\python.exe tools\resolve_coordinates.py

# (5) 업로드용 XLSX + 시트별 CSV
.venv\Scripts\python.exe tools\build_workbook.py

# (6) Google Sheets 업로드
.venv\Scripts\python.exe tools\upload_to_sheets.py

# (7) 대시보드 빌드 + 서빙
.venv\Scripts\python.exe tools\build_dashboard.py
.venv\Scripts\python.exe -m http.server 8080 --directory dashboard
#   → http://localhost:8080/
```

## 3. 데이터 흐름

```
data/raw/         원본 문서 (수정 금지, SHA-256 기록)
data/extracted/   PDF 텍스트·표·OCR, 허가대장·회의록·KPX 추출 결과
data/interim/     정규화·병합 중간 결과, 검수필요_*.csv, 지오코딩 캐시
data/processed/   발전소_통합목록.csv, 허가이력.csv, 계통계약.csv, XLSX, 시트 CSV
dashboard/        VWorld 대시보드 (index.html 은 빌드 산출물)
```

## 4. 출처와 판정 원칙

- **모집단**: 전기위원회 회의록 + 3MW초과 허가대장. KPX 추진현황은 사업자 자율제출
  취합자료이므로 보조 출처로만 사용(결측 칼럼 보완).
- **최초허가일·변경이력**: 전기위원회 자료가 정본. KPX `발전사업(변경)허가`는
  "가장 최근 허가일"이라 최초허가일로 쓰지 않는다.
- **발전원 육상/해상**: 사업명 → KPX 형식 → 법인명 → 위치 해상표지 → 육상 지번
  순으로 원문 근거를 찾고, 근거 없으면 `풍력_구분미상`으로 검수.
- **좌표**: 해상풍력은 허가대장 사업구역 좌표(폴리곤 중심) 우선, 없으면 `좌표없음`
  (임의로 바다에 찍지 않음). 육상은 지번→도로명→읍면동 지오코딩.
- **OCR**: Windows 내장 OCR 결과는 `데이터상태=검수필요`. 원문 대조로 확정 시
  `OCR+원문확정` → `확정` 승격.

## 5. 정기 업데이트

1. 전기위원회에 새 회차 개최결과·회의록, 새 반기 KPX 자료가 올라오면
   `download_korec_attachments.py` 재실행 (증분 — 기존 파일은 건너뜀).
2. `run_pipeline.py` → `resolve_coordinates.py` → `build_workbook.py`
   → `upload_to_sheets.py` 순서로 재실행. 프로젝트ID는 사업명+시군구 해시라
   자료가 갱신돼도 불변이다.
3. 업로드는 기존 시트를 `_bak_*`로 백업한 뒤 교체한다.

## 6. 산출물

- Google Sheets 10개 시트 (태양광·육상풍력·해상풍력·발전소통합목록·허가이력·
  계통계약·출처목록·검수필요·데이터사전·변경이력)
- `data/processed/전남광주_발전사업_원천데이터.xlsx` + 시트별 CSV
- VWorld 지도 대시보드
- 검수필요 목록 (OCR·중복의심·지역판정·풍력구분)

## 7. Streamlit 앱 & 배포

### 로컬 실행
```bash
.venv\Scripts\streamlit.exe run streamlit_app.py
```
`.env` 의 VWORLD_API_KEY 를 읽어 지도를 렌더한다.

### GitHub 공개 저장소 배포 정책
- **커밋 포함**: 코드(`src/`,`tools/`,`streamlit_app.py`), 대시보드 템플릿, `dashboard/data.json`
- **커밋 제외**(`.gitignore`): 모든 `*.env`·키 파일·`secrets/`·서비스계정 JSON,
  원본/중간 데이터(`data/raw`,`extracted`,`interim`,`processed`), 리포트, VWorld 키가 주입된 `index.html`
- 발전소 데이터는 대시보드용 `data.json` 만 공개(사업주체·최대주주·좌표 포함, 공공자료 기반)

### Streamlit Community Cloud 배포
1. GitHub 공개 저장소에 push
2. share.streamlit.io 에서 저장소 연결, `streamlit_app.py` 지정
3. **App settings → Secrets** 에 입력(`.streamlit/secrets.toml.example` 참고):
   ```
   VWORLD_API_KEY = "발급키"
   VWORLD_DOMAIN  = "your-app.streamlit.app"
   ```
4. **VWorld 마이페이지에 배포 도메인(`*.streamlit.app`) 등록** — 안 하면 지도 미표시
5. 배포용 의존성은 `requirements-app.txt`(경량). 루트 `requirements.txt` 는 전체 파이프라인용
