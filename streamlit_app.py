"""전남·광주 발전사업 현황 — Streamlit 대시보드.

필터·KPI·표는 Streamlit 네이티브, 지도는 VWorld HTML 을 런타임에 조립해 임베드한다.
VWorld/Google 키는 st.secrets 또는 .env 에서 읽어 코드·저장소에 남기지 않는다.

실행:  .venv\\Scripts\\streamlit.exe run streamlit_app.py
배포:  GitHub 공개 저장소 + Streamlit Community Cloud
       - Secrets 에 VWORLD_API_KEY, VWORLD_DOMAIN 입력
       - VWorld 마이페이지에 배포 도메인(*.streamlit.app) 등록 필요
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "dashboard" / "data.json"
MAP_TEMPLATE = ROOT / "dashboard" / "map_embed.template.html"

COLORS = {"태양광": "#c98a2b", "육상풍력": "#4a7c59", "해상풍력": "#3f6b8c",
          "풍력_구분미상": "#8a9199", "복합_검수필요": "#8a9199"}
SRC_ORDER = ["태양광", "육상풍력", "해상풍력", "풍력_구분미상", "복합_검수필요"]

st.set_page_config(page_title="전남·광주 발전사업 현황", layout="wide",
                   initial_sidebar_state="expanded")


# ── 데이터/키 로딩 ──────────────────────────────────────────────────
@st.cache_data
def load_data() -> tuple[list[dict], dict]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return payload["plants"], payload.get("meta", {})


def get_secret(name: str, default: str = "") -> str:
    """st.secrets 우선, 없으면 .env/환경변수. 저장소에는 어느 쪽도 커밋 안 함."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    val = os.getenv(name)
    if val:
        return val
    # 로컬 개발 편의: .env 파싱 (배포 시엔 secrets 사용)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return default


def clean_domain(raw: str) -> str:
    """VWorld domain 파라미터용으로 정규화: 프로토콜·끝 슬래시 제거.
    Secrets 에 'https://xxx.streamlit.app/' 처럼 넣어도 'xxx.streamlit.app' 로 맞춘다."""
    d = (raw or "").strip()
    d = d.replace("https://", "").replace("http://", "")
    return d.rstrip("/")


plants, meta = load_data()
df = pd.DataFrame(plants)
VWORLD_KEY = get_secret("VWORLD_API_KEY").strip()
VWORLD_DOMAIN = clean_domain(get_secret("VWORLD_DOMAIN", "localhost"))


# ── 사이드바 필터 ───────────────────────────────────────────────────
st.sidebar.markdown("### 필터")

src_sel = st.sidebar.multiselect(
    "발전원", [s for s in SRC_ORDER if (df["src"] == s).any()],
    default=[s for s in SRC_ORDER if (df["src"] == s).any()])

regions = sorted(x for x in df["region"].dropna().unique() if x)
region_sel = st.sidebar.selectbox("지역", ["전체"] + regions)

if region_sel != "전체":
    sgg_opts = sorted(x for x in df[df["region"] == region_sel]["sgg"].dropna().unique() if x)
else:
    sgg_opts = sorted(x for x in df["sgg"].dropna().unique() if x)
sgg_sel = st.sidebar.selectbox("시군구", ["전체"] + sgg_opts)

operator_q = st.sidebar.text_input("사업주체 검색", "")

status_opts = sorted(x for x in df["agendaType"].dropna().unique() if x)
status_sel = st.sidebar.selectbox("최근 안건유형", ["전체"] + status_opts)

basemap = st.sidebar.selectbox(
    "배경지도",
    ["위성지도 (지명 표시)", "위성지도 (사진만)", "일반지도", "일반지도 (회색)"])
BASEMAP_CODE = {"위성지도 (지명 표시)": "photo-hybrid", "위성지도 (사진만)": "photo",
                "일반지도": "graphic", "일반지도 (회색)": "graphic-gray"}[basemap]

st.sidebar.caption("지도의 점을 클릭하면 발전소 정보가 표시됩니다.")


# ── 필터 적용 ───────────────────────────────────────────────────────
mask = df["src"].isin(src_sel)
if region_sel != "전체":
    mask &= df["region"] == region_sel
if sgg_sel != "전체":
    mask &= df["sgg"] == sgg_sel
if operator_q.strip():
    mask &= df["operator"].fillna("").str.contains(operator_q.strip())
if status_sel != "전체":
    mask &= df["agendaType"] == status_sel
fdf = df[mask].copy()


# ── 헤더 ────────────────────────────────────────────────────────────
left, right = st.columns([3, 2])
with left:
    st.markdown("#### 전남·광주 발전사업 현황")
    st.caption("태양광 · 육상풍력 · 해상풍력")
with right:
    st.caption(f"데이터 기준일 {meta.get('기준일', '—')} · "
               f"출처 전기위원회 · 3MW초과 허가대장 · KPX")


# ── KPI ─────────────────────────────────────────────────────────────
def mw_sum(frame):
    return pd.to_numeric(frame["mw"], errors="coerce").sum()

k = st.columns(5)
k[0].metric("전체 사업", f"{len(fdf):,}")
k[1].metric("설비용량 합계", f"{mw_sum(fdf):,.0f} MW")
for i, s in enumerate(["태양광", "해상풍력", "육상풍력"]):
    sub = fdf[fdf["src"] == s]
    k[i + 2].metric(s, f"{len(sub):,}", f"{mw_sum(sub):,.0f} MW")


# ── 지도 (VWorld 임베드) ────────────────────────────────────────────
if not VWORLD_KEY:
    st.warning("VWorld API 키가 설정되지 않았습니다. 로컬은 .env, 배포는 Secrets 에 "
               "VWORLD_API_KEY 를 입력하세요. 아래 표는 정상 동작합니다.")
else:
    map_cols = ["id", "name", "src", "region", "sgg", "operator",
                "mw", "firstPermit", "round", "lat", "lon"]
    slim = fdf[[c for c in map_cols if c in fdf.columns]]
    # pandas to_json 이 NaN→null 로 올바르게 직렬화한다. json.dumps 는 NaN 을
    # 그대로 내보내 브라우저에서 좌표가 NaN 이 되고 마커 생성이 실패한다.
    plants_json = slim.to_json(orient="records", force_ascii=False)
    html = (MAP_TEMPLATE.read_text(encoding="utf-8")
            .replace("__VWORLD_KEY__", VWORLD_KEY)
            .replace("__VWORLD_DOMAIN__", VWORLD_DOMAIN)
            .replace("__BASEMAP__", BASEMAP_CODE)
            .replace("__PLANTS_JSON__", plants_json))
    components.html(html, height=560, scrolling=False)


# ── 표 ──────────────────────────────────────────────────────────────
st.markdown("##### 발전소 목록")
show = fdf.rename(columns={
    "name": "발전소명", "src": "발전원", "region": "지역", "sgg": "시군구",
    "operator": "사업주체", "owner": "최대주주", "mw": "설비용량(MW)",
    "cost": "총사업비(억원)", "firstPermit": "최초허가일", "agendaType": "최근안건유형",
    "status": "데이터상태", "loc": "허가위치",
})
cols = ["발전소명", "발전원", "지역", "시군구", "사업주체", "최대주주",
        "설비용량(MW)", "총사업비(억원)", "최초허가일", "최근안건유형", "데이터상태", "허가위치"]
st.dataframe(show[[c for c in cols if c in show.columns]],
             use_container_width=True, hide_index=True, height=380)

st.caption(f"필터 결과 {len(fdf):,}건 / 전체 {len(df):,}건 · "
           "숫자·명칭은 공식 원문 기반이며 검수 상태는 데이터상태 열 참조")
