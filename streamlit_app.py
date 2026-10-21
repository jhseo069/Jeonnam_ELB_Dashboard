"""전남·광주·고창 발전사업 현황 — Streamlit 대시보드(전북 고창군 포함).

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
SUBST_PATH = ROOT / "dashboard" / "substations.json"

COLORS = {"태양광": "#c98a2b", "육상풍력": "#4a7c59", "해상풍력": "#3f6b8c",
          "풍력_구분미상": "#8a9199", "복합_검수필요": "#8a9199"}
SRC_ORDER = ["태양광", "육상풍력", "해상풍력", "풍력_구분미상", "복합_검수필요"]

st.set_page_config(page_title="전남·광주·고창 발전사업 현황", layout="wide",
                   initial_sidebar_state="expanded")


# ── 데이터/키 로딩 ──────────────────────────────────────────────────
@st.cache_data
def load_data(mtime: float) -> tuple[list[dict], dict]:
    """mtime 을 캐시 키로 받아, data.json 이 갱신되면 캐시를 새로 읽는다."""
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


plants, meta = load_data(DATA_PATH.stat().st_mtime)


@st.cache_data
def load_subst(mtime: float) -> dict:
    return json.loads(SUBST_PATH.read_text(encoding="utf-8")) if SUBST_PATH.exists() else {}


subst_data = load_subst(SUBST_PATH.stat().st_mtime if SUBST_PATH.exists() else 0.0)
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

# 심의결과: 안건유형(제목 종류)과 별개로 회의록 결과 제목 기준(허가/조건부 허가/심의 보류 …)
RESULT_ORDER = ["허가", "조건부 허가", "심의 보류", "심의 연기", "불허가", "허가 취소",
                "인가", "조건부 인가", "불인가", "승인", "조건부 승인", "반려", "각하", "인가 철회"]
present_results = set(df.get("result", pd.Series(dtype=str)).fillna("")) - {""}
result_opts = ([r for r in RESULT_ORDER if r in present_results]
               + sorted(present_results - set(RESULT_ORDER)))
result_sel = st.sidebar.selectbox("최근 심의결과", ["전체"] + result_opts)

basemap = st.sidebar.selectbox(
    "배경지도",
    ["위성지도 (지명 표시)", "위성지도 (사진만)", "일반지도", "일반지도 (회색)"])
BASEMAP_CODE = {"위성지도 (지명 표시)": "photo-hybrid", "위성지도 (사진만)": "photo",
                "일반지도": "graphic", "일반지도 (회색)": "graphic-gray"}[basemap]

SORT_OPTIONS = {
    "최근 심의순": ("round", False),
    "최초허가일 최신순": ("firstPermit", False),
    "설비용량 큰 순": ("mw", False),
    "발전소명 가나다순": ("name", True),
}
sort_sel = st.sidebar.selectbox("목록 정렬", list(SORT_OPTIONS))

show_subst = st.sidebar.checkbox("변전소 표시 (고창·홍농, 한전ON)", value=True)
st.sidebar.caption("지도의 점을 클릭하면 발전소 정보가, 마름모를 클릭하면 변전소 계통 여유가 표시됩니다.")


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
if result_sel != "전체":
    mask &= df["result"] == result_sel
fdf = df[mask].copy()


# ── 헤더 ────────────────────────────────────────────────────────────
left, right = st.columns([3, 2])
with left:
    st.markdown("#### 전남·광주·고창 발전사업 현황")
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
                "mw", "firstPermit", "round", "result", "lat", "lon"]
    slim = fdf[[c for c in map_cols if c in fdf.columns]]
    # pandas to_json 이 NaN→null 로 올바르게 직렬화한다. json.dumps 는 NaN 을
    # 그대로 내보내 브라우저에서 좌표가 NaN 이 되고 마커 생성이 실패한다.
    plants_json = slim.to_json(orient="records", force_ascii=False)
    html = (MAP_TEMPLATE.read_text(encoding="utf-8")
            .replace("__VWORLD_KEY__", VWORLD_KEY)
            .replace("__VWORLD_DOMAIN__", VWORLD_DOMAIN)
            .replace("__BASEMAP__", BASEMAP_CODE)
            .replace("__PLANTS_JSON__", plants_json)
            .replace("__SUBST_JSON__", json.dumps(subst_data if show_subst else {}, ensure_ascii=False)))
    components.html(html, height=560, scrolling=False)


# ── 표 ──────────────────────────────────────────────────────────────
# ── 변전소 계통 여유 (한전ON) ──────────────────────────────────────────
if show_subst and subst_data.get("substations"):
    st.markdown("##### 변전소 계통 여유 (고창·홍농)")
    def _free(s):
        d = s.get("dist") or {}
        if d.get("capa_kw") is None or d.get("recv_kw") is None:
            return None
        return round(max(0.0, (d["capa_kw"] - d["recv_kw"]) / 1000), 1)
    def _trans(s):
        ys = (s.get("trans154") or {}).get("yearly") or []
        if not ys:
            return "—"
        frees = [y for y in ys if (y.get("mw") or 0) > 0]
        return f"{frees[0]['year']}년 {frees[0]['mw']}MW~" if frees else f"{ys[0]['year']}~{ys[-1]['year']} 없음"
    rows = []
    for s in subst_data["substations"]:
        d = s.get("dist") or {}
        b = s.get("breaker") or {}
        rows.append({
            "변전소": s["name"], "지역": s.get("region"), "상태": s["status"], "읍면": s.get("emd"),
            "변전소 여유(MW)": _free(s),
            "접속기준(MW)": round(d["capa_kw"] / 1000, 1) if d.get("capa_kw") else None,
            "접수기준(MW)": round(d["recv_kw"] / 1000, 1) if d.get("recv_kw") else None,
            "배전선로": len(s.get("lines") or []),
            "송전망 154kV 여유": _trans(s),
            "차단기 22.9kV": f"{b['kv22_free']:.0f}/{b['kv22_total']:.0f}" if b else "—",
            "차단기 154kV": f"{b['kv154_free']:.0f}/{b['kv154_total']:.0f}" if b else "—",
            "가압 예정": (s.get("plan") or {}).get("가압", ""),
            "지도": ("표시(면 단위 대표위치)" if s.get("approx") else "표시") if s.get("lat") is not None else "위치 미정",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"출처 한전ON 공개 계통정보 · 수집 {subst_data.get('collected', '—')} · "
               "접속 가능 여부를 확정하는 자료가 아닌 참고자료 · 건설 중 변전소 일정·위치는 한전 송변전 건설 정보공개 기준")

st.markdown("##### 발전소 목록")
# 정렬: 값 없는 행(허가대장전용 등 회차·허가일 없음)은 항상 뒤로
sort_col, ascending = SORT_OPTIONS[sort_sel]
sdf = fdf.copy()
if sort_col in ("round", "mw"):
    sdf["_k"] = pd.to_numeric(sdf[sort_col], errors="coerce")
else:
    sdf["_k"] = sdf[sort_col].replace("", None)
sdf = sdf.sort_values("_k", ascending=ascending, na_position="last", kind="stable")
sdf["최근심의"] = pd.to_numeric(sdf["round"], errors="coerce").map(
    lambda r: f"제{int(r)}차" if pd.notna(r) else "")
show = sdf.rename(columns={
    "name": "발전소명", "src": "발전원", "region": "지역", "sgg": "시군구",
    "operator": "사업주체", "owner": "최대주주", "mw": "설비용량(MW)",
    "cost": "총사업비(억원)", "firstPermit": "최초허가일", "agendaType": "최근안건유형", "result": "심의결과",
    "status": "데이터상태", "loc": "허가위치",
})
cols = ["발전소명", "발전원", "지역", "시군구", "최근심의", "최근안건유형", "심의결과", "사업주체",
        "최대주주", "설비용량(MW)", "총사업비(억원)", "최초허가일", "데이터상태", "허가위치"]
st.dataframe(show[[c for c in cols if c in show.columns]],
             use_container_width=True, hide_index=True, height=380)

st.caption(f"필터 결과 {len(fdf):,}건 / 전체 {len(df):,}건 · "
           "숫자·명칭은 공식 원문 기반이며 검수 상태는 데이터상태 열 참조")
