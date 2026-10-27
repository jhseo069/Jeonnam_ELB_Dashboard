r"""한전ON 공개 계통정보로 변전소 여유용량을 수집한다 → dashboard/substations.json

대상: config/substations.json (지역 묶음별: 전북 고창군, 전남 영광군 홍농 …).
출처(한전ON 화면이 쓰는 공개 조회 API, 로그인 불필요):
  - 154kV 송전망 여유(연도별)·발전허가 신청용량·접속예정 사업자  /ew/cpct/retrieveTransCpct154kV
  - 22.9kV·154kV 차단기 여유 Bay                                  /ew/cpct/retrieveCbrOvplsInfo
  - 전력공급 여유(22.9kV·154kV, 연도별)                           /ew/api/energy/subSt23, subSt154
  - 변전소·주변압기·배전선로 접속 용량(주소 단위 조회)              /ew/cpct/retrieveMeshNo
배전선로는 주소 1곳당 담당 선로 1개만 돌려주므로, 군 안의 모든 리를 돌며 번지
몇 곳씩 조회해 선로를 모은다(군 밖 주소만 담당하는 선로는 빠질 수 있음).

한전ON 고지: 단순 참고자료이며 3·6·9·12월 분기(배전은 수시) 갱신. 실제 접속 가능
여부는 별도 검토 필요.

실행: .venv\Scripts\python.exe tools\collect_kepco_substations.py              (전체)
      .venv\Scripts\python.exe tools\collect_kepco_substations.py --region 영광   (해당 지역만, 나머지는 이전 값 유지)
      .venv\Scripts\python.exe tools\collect_kepco_substations.py --locate-only   (위치만 갱신, 한전ON 재조회 없음)
      .venv\Scripts\python.exe tools\collect_kepco_substations.py --bigdata-only  (분산전원연계정보로 빠진 선로만 보강)

보강: 전력데이터개방포털 분산전원연계정보(.env KEPCO_BIGDATA_API_KEY, config bigdata_code)로
      표본 조회에서 빠진 배전선로를 채운다(전체 수집 때 자동 실행).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
CONFIG = ROOT / "config/substations.json"
OUT = ROOT / "dashboard/substations.json"
BASE = "https://online.kepco.co.kr"
DELAY = 0.3
JIBUN_PER_LI = 3          # 리마다 조회할 번지 수(앞·중간·뒤)


class Kepco:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "jeonnam-re-data-collector/0.1 (contact: jhseo@kchglobal.co.kr)"),
            "Content-Type": "application/json; charset=UTF-8",
            "Referer": f"{BASE}/EWM092D00",
        })
        self.s.get(f"{BASE}/EWM092D00", timeout=30)
        self.calls = 0

    def post(self, path: str, body: dict) -> dict:
        for attempt in range(3):
            try:
                time.sleep(DELAY)
                self.calls += 1
                r = self.s.post(BASE + path, data=json.dumps(body, ensure_ascii=False).encode(),
                                timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        return {}

    def addr_list(self, gbn: int, addr: dict) -> list[str]:
        body = {"dma_addrGbn": {"gbn": gbn, "addr_do": addr.get("do", ""),
                                "addr_si": addr.get("si", ""), "addr_gu": addr.get("gu", ""),
                                "addr_lidong": addr.get("lidong", ""), "addr_li": addr.get("li", ""),
                                "addr_jibun": ""}}
        key = {2: "ADDR_LIDONG", 3: "ADDR_LI", 4: "ADDR_JIBUN"}[gbn]
        return [row.get(key) for row in self.post("/ew/cpct/retrieveAddrGbn", body).get("dlt_addrGbn", [])
                if row.get(key)]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def yearly(row: dict, base_year: int) -> list[dict]:
    keys = ["THIS_YY", "ONE_YY", "TWO_YY", "THR_YY", "FOR_YY", "FIV_YY", "SIX_YY"]
    return [{"year": base_year + i, "mw": _num(row.get(k))} for i, k in enumerate(keys)]


def locate(subs: list[dict]) -> None:
    """위치: config 의 lat/lon 우선, 없으면 address 를 VWorld 로 좌표 변환. 둘 다 없으면 지도 미표시."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    geocoder = None
    for s in subs:
        if s.get("lat") is None and s.get("address"):
            if geocoder is None:
                from transforms.vworld_geocode import VWorldGeocoder
                geocoder = VWorldGeocoder()
            g = geocoder.geocode(s["address"])
            if g.get("위도") is not None:
                s["lat"], s["lon"] = round(g["위도"], 6), round(g["경도"], 6)


def all_substations(cfg: dict) -> list[dict]:
    return [sub for region in cfg["regions"] for sub in region["substations"]]


def locate_only() -> None:
    """한전ON 재조회 없이 config 의 주소·좌표만 substations.json 에 반영한다."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    subs = all_substations(cfg)
    locate(subs)
    data = json.loads(OUT.read_text(encoding="utf-8"))
    by_code = {s["code"]: s for s in subs}
    region_of = {s["code"]: r["name"] for r in cfg["regions"] for s in r["substations"]}
    have = {s["code"]: s for s in data["substations"]}
    ordered = []
    for c, cs in by_code.items():          # config 순서 유지. 새로 추가된 변전소는 한전ON 값 없이 넣는다
        s = have.get(c) or {"code": c, "emd": None, "dist": None, "lines": [],
                            "trans154": {"yearly": []}, "breaker": None, "supply": {}}
        s["name"], s["status"], s["region"] = cs["name"], cs["status"], region_of[c]
        for k in ("address", "location_source", "lat", "lon", "plan", "kv", "approx", "tlines"):
            s[k] = cs.get(k)
        if cs.get("emd"):
            s["emd"] = cs["emd"]
        ordered.append(s)
        print(f"{s['name']}: {s.get('lat')}, {s.get('lon')}  ({s.get('address') or '위치 미정'})")
    data["substations"] = ordered
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_region(k: Kepco, region: dict, year: int, subst: dict, lines: dict) -> list[dict]:
    sido, sigg = region["sido_code"], region["sigg_code"]
    print(f"[{region['name']}]")

    # 1) 154kV 송전망 여유 — 연도 인자는 조회 기간 끝 해(화면 기본값과 동일하게 +6)
    # 가끔 빈 목록을 돌려주므로(2026-10-22 장흥 조회) 빈 응답이면 한 번 더 조회한다
    for _ in range(3):
        trans = {r["PSPWPMNGNO"]: r for r in k.post("/ew/cpct/retrieveTransCpct154kV", {
            "dma_reqParam": {"sido_code": sido, "sigg_code": sigg, "emd_code": "", "year": str(year + 6)}
        }).get("dlt_resultList", [])}
        if trans:
            break
        time.sleep(3)
    # 2) 차단기 여유 Bay (B009=22.9kV, B005=154kV)
    cbr = {r["S_CD"]: r for r in k.post("/ew/cpct/retrieveCbrOvplsInfo", {
        "dma_reqParam": {"sido_code": sido, "sigg_code": sigg, "emd_code": ""}
    }).get("dlt_resultList", [])}
    # 3) 전력공급 여유
    sup23 = {r["PSPWPMNGNO"]: r for r in (k.post("/ew/api/energy/subSt23", {
        "dma_subSt23": {"sidoCode": sido, "siggCode": sigg, "emdCode": "", "year": str(year)}
    }).get("dma_subSt23list") or [])}
    sup154 = {r["PSPWPMNGNO"]: r for r in (k.post("/ew/api/energy/subSt154", {
        "dma_subSt154": {"sidoCode": sido, "siggCode": sigg, "emdCode": "", "year": str(year)}
    }).get("dma_subSt154list") or [])}

    # 4) 배전: 군 전역 주소 표본 조회로 변전소/주변압기/배전선로 수집
    # subst/lines 는 모든 지역이 공유한다: 경계 변전소(홍농 등)는 이웃 군 주소도 담당하므로
    # 다른 지역 조회에서 나온 선로까지 합쳐야 빠짐이 없다.
    addr = dict(region["addr"])
    lidongs = k.addr_list(2, addr)
    print(f"  동/면 {len(lidongs)}개 조회 시작")
    for lidong in lidongs:
        lis = k.addr_list(3, {**addr, "lidong": lidong}) or [""]
        for li in lis:
            jibuns = k.addr_list(4, {**addr, "lidong": lidong, "li": li})
            if not jibuns:
                continue
            n = len(jibuns)
            picks = sorted({jibuns[0], jibuns[n // 2], jibuns[-1]})[:JIBUN_PER_LI]
            for jibun in picks:
                res = k.post("/ew/cpct/retrieveMeshNo", {"dma_reqParam": {
                    "searchCondition": "address", "do": addr["do"], "si": addr["si"],
                    "gu": addr["gu"], "lidong": lidong, "li": li, "jibun": jibun}})
                for r in res.get("dlt_resultList", []):
                    sc = r.get("SUBST_CD")
                    if not sc:
                        continue
                    subst.setdefault(sc, {
                        "name": r.get("SUBST_NM"), "capa_kw": _num(r.get("SUBST_CAPA")),
                        "recv_kw": _num(r.get("SUBST_PWR")), "plan_kw": _num(r.get("G_SUBST_CAPA"))})
                    key = (sc, str(r.get("MTR_NO")), str(r.get("DL_CD")))
                    lines.setdefault(key, {
                        "mtr": str(r.get("MTR_NO")), "mtr_capa_kw": _num(r.get("MTR_CAPA")),
                        "mtr_recv_kw": _num(r.get("MTR_PWR")), "mtr_plan_kw": _num(r.get("G_MTR_CAPA")),
                        "dl": r.get("DL_NM"), "dl_capa_kw": _num(r.get("DL_CAPA")),
                        "dl_recv_kw": _num(r.get("DL_PWR")), "dl_plan_kw": _num(r.get("G_DL_CAPA")),
                        "sample": f"{lidong} {li} {jibun}".strip()})
        print(f"    {lidong}: 누적 변전소 {len(subst)} / 선로 {len(lines)} (호출 {k.calls})", flush=True)

    return {"trans": trans, "cbr": cbr, "sup23": sup23, "sup154": sup154}


def build_outputs(region: dict, meta: dict, subst: dict, lines: dict, year: int) -> list[dict]:
    trans, cbr, sup23, sup154 = meta["trans"], meta["cbr"], meta["sup23"], meta["sup154"]
    locate(region["substations"])
    out = []
    for s in region["substations"]:
        c = s["code"]
        t, b, d = trans.get(c, {}), cbr.get(c, {}), subst.get(c, {})
        base_year = int(t.get("RVW_YY") or year)
        my_lines = sorted((v for key, v in lines.items() if key[0] == c),
                          key=lambda v: (v["mtr"], v["dl"] or ""))
        out.append({
            "code": c, "name": s["name"], "status": s["status"], "region": region["name"],
            "address": s.get("address", ""), "location_source": s.get("location_source", ""),
            "lat": s.get("lat"), "lon": s.get("lon"), "plan": s.get("plan"),
            "kv": s.get("kv"), "approx": s.get("approx"), "tlines": s.get("tlines"),
            "emd": s.get("emd") or t.get("EMD_NM") or b.get("EMD_NM"),   # config 값 우선(한전ON 읍면이 실제 소재지와 다른 경우)
            "dist": d or None,
            "lines": my_lines,
            "trans154": {"yearly": yearly(t, base_year) if t else [],
                         "permit_req_mw": _num(t.get("SIXYEARRVWCPCT")),
                         "connect_wait": _num(t.get("ETC"))},
            "breaker": {"kv22_free": _num(b.get("B009_FUTU_BAY")), "kv22_total": _num(b.get("B009_TOTAL_BAY")),
                        "kv154_free": _num(b.get("B005_FUTU_BAY")), "kv154_total": _num(b.get("B005_TOTAL_BAY"))}
                       if b else None,
            "supply": {"kv22": yearly(sup23[c], int(sup23[c].get("CRTR_YY") or year)) if c in sup23 else [],
                       "kv154": yearly(sup154[c], int(sup154[c].get("CRTR_YY") or year)) if c in sup154 else []},
        })
        located = (s.get("lat"), s.get("lon")) if s.get("lat") is not None else "미정"
        print(f"  {s['name']}({c}): 선로 {len(my_lines)}개, 배전 {'O' if d else 'X'}, "
              f"송전 {'O' if t else 'X'}, 차단기 {'O' if b else 'X'}, 위치 {located}")
    return out


BIGDATA_URL = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"


def bigdata_rows(key: str, metro: str, city: str) -> list[dict]:
    """전력데이터개방포털 분산전원연계정보(군 단위 전체 선로). 법정동 옛 코드 사용(전남=46, 영광 870, 장흥 800).
    오류 메시지에 인증키가 섞이지 않도록 URL 은 출력하지 않는다."""
    try:
        r = requests.get(BIGDATA_URL, params={"metroCd": metro, "cityCd": city, "apiKey": key,
                                              "returnType": "json"}, timeout=120)
        j = r.json()
    except Exception as e:
        raise RuntimeError(f"분산전원연계 조회 실패({metro}/{city}): {type(e).__name__}") from None
    if r.status_code != 200:
        raise RuntimeError(f"분산전원연계 조회 실패({metro}/{city}): HTTP {r.status_code} {j.get('errMsg', '')}")
    return j.get("data", [])


def supplement_from_bigdata(cfg: dict, subs: list[dict]) -> None:
    """한전ON 주소 표본 조회에서 빠진 배전선로를 분산전원연계정보로 채운다.
    이 API 는 용량(js*)을 0으로 주고 누적 연계량·여유(vol)만 주므로, 새로 넣는 선로는
    기준용량 없이 접수(누적)·여유만 기록한다. 변전소 누적 연계량이 한전ON 값과 다른 행은
    같은 코드의 다른 설비로 보고 넣지 않는다(예: 홍농 코드의 '연구'·'상하' 135.7MW)."""
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / ".env")
    key = (env.get("KEPCO_BIGDATA_API_KEY") or env.get("KEPCO_BIGDATA_API") or "").strip().strip('"')
    if not key:
        print("  (분산전원연계정보: .env 에 KEPCO_BIGDATA_API_KEY 없음 — 건너뜀)")
        return
    by_code = {s["code"]: s for s in subs}
    seen = set()
    for region in cfg["regions"]:
        code = region.get("bigdata_code")
        if not code or tuple(code) in seen:
            continue
        seen.add(tuple(code))
        rows = bigdata_rows(key, *code)
        added = skipped = 0
        for r in rows:
            s = by_code.get(r.get("substCd"))
            if not s or not s.get("dist"):
                continue
            if abs((_num(r.get("substPwr")) or 0) - (s["dist"].get("recv_kw") or 0)) > 1:
                skipped += 1
                continue
            mtr, dl = str(r.get("mtrNo")), r.get("dlNm")
            hit = next((l for l in s["lines"] if l["mtr"] == mtr and l["dl"] == dl), None)
            if hit:
                hit["dl_free_kw"] = _num(r.get("vol3"))
                continue
            s["lines"].append({
                "mtr": mtr, "mtr_capa_kw": None, "mtr_recv_kw": _num(r.get("mtrPwr")), "mtr_plan_kw": None,
                "dl": dl, "dl_capa_kw": None, "dl_recv_kw": _num(r.get("dlPwr")), "dl_plan_kw": None,
                "dl_free_kw": _num(r.get("vol3")), "source": "전력데이터개방포털 분산전원연계정보"})
            added += 1
        for s in subs:
            s["lines"].sort(key=lambda v: (v["mtr"], v["dl"] or ""))
        print(f"  분산전원연계정보 {region['name']}({'/'.join(code)}): {len(rows)}행, 빠진 선로 {added}개 추가, "
              f"누적량 불일치 {skipped}행 제외")


def bigdata_only() -> None:
    """한전ON 재조회 없이 기존 substations.json 에 분산전원연계정보 선로만 보강한다."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    data = json.loads(OUT.read_text(encoding="utf-8"))
    supplement_from_bigdata(cfg, data["substations"])
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    """전체 수집. '--region 영광' 처럼 주면 해당 지역만 다시 수집해 기존 결과에 합친다
    (이 경우 경계 변전소의 이웃 군 선로는 빠질 수 있으니 정기 갱신은 전체 수집 권장)."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    only = sys.argv[sys.argv.index("--region") + 1] if "--region" in sys.argv else None
    regions = [r for r in cfg["regions"] if not only or only in r["name"]]
    k = Kepco()
    year = date.today().year
    subst: dict[str, dict] = {}
    lines: dict[tuple, dict] = {}
    metas = [(region, collect_region(k, region, year, subst, lines)) for region in regions]
    collected = {}
    for region, meta in metas:
        for sub in build_outputs(region, meta, subst, lines, year):
            collected[sub["code"]] = sub

    previous = json.loads(OUT.read_text(encoding="utf-8")).get("substations", []) if OUT.exists() else []
    prev = {s["code"]: s for s in previous}
    ordered = []
    for sub in all_substations(cfg):            # config 순서 유지, 이번에 안 모은 것은 이전 값
        c = sub["code"]
        if c in collected:
            ordered.append(collected[c])
        elif c in prev:
            ordered.append(prev[c])
    supplement_from_bigdata({"regions": regions}, [s for s in ordered if s["code"] in collected])
    OUT.write_text(json.dumps({"collected": date.today().isoformat(),
                               "source": "한전ON(online.kepco.co.kr) 재생e 연계 여유용량·배전선로 여유용량·차단기 여유 Bay·전력공급 여유용량",
                               "substations": ordered}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장 {OUT} (변전소 {len(ordered)}곳, 한전ON 호출 {k.calls}회)")


if __name__ == "__main__":
    if "--locate-only" in sys.argv:
        locate_only()
    elif "--bigdata-only" in sys.argv:
        bigdata_only()
    else:
        main()
