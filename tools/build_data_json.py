r"""처리 결과 CSV -> dashboard/data.json 생성.

지금까지 data.json 은 초기 구축 때 임시로 만들어졌고 커밋된 생성 도구가 없었다.
증분 갱신(새 회차 반영) 때 재생성할 수 있도록 이 도구로 고정한다.

입력 (data/processed)
  발전소_통합목록.csv  (resolve_coordinates 로 위경도까지 채워진 상태)
  허가이력.csv         (프로젝트ID별 심의 이력 -> plant.history)
  계통계약.csv         (프로젝트ID별 계통계약)
출력
  dashboard/data.json  ({plants:[...], meta:{...}})

build_dashboard.py 는 이 data.json 을 읽어 index.html/data.json.js 로 감싼다.
같은 입력이면 같은 결과가 나온다(지침 §19).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data/processed"
OUT = ROOT / "dashboard/data.json"
기준일 = "2026-09-09"  # 반영된 최신 전기위원회 자료 게시일(제328차 개최결과·제327차 회의록)


def _s(v) -> str:
    """문자열 필드: 결측은 빈 문자열."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v)


def _f(v):
    """숫자 필드: 결측은 None(JSON null). NaN 을 그대로 내보내지 않는다."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _round3(v):
    f = _f(v)
    return None if f is None else round(f, 3)


def build_history(hist: pd.DataFrame) -> dict[str, list[dict]]:
    """프로젝트ID별 심의 이력(허가이력.csv 행 순서 보존)."""
    out: dict[str, list[dict]] = {}
    for row in hist.to_dict("records"):
        pid = row.get("프로젝트ID")
        if pid is None or (isinstance(pid, float) and math.isnan(pid)):
            continue                              # 대상 사업에 매칭 안 된 안건은 제외
        out.setdefault(str(pid), []).append({
            "round": int(row["전기위원회회차"]) if _f(row.get("전기위원회회차")) is not None else None,
            "type": _s(row.get("안건유형")),
            "agenda": _s(row.get("안건명")),
            "owner": _s(row.get("최대주주")),
            "transferor": _s(row.get("양도인")),
            "transferee": _s(row.get("양수인")),
        })
    return out


def build_contracts(con: pd.DataFrame) -> dict[str, list[dict]]:
    """프로젝트ID별 계통계약(옛 data.json 은 비어 있었으나, 매칭분은 실어 준다)."""
    out: dict[str, list[dict]] = {}
    for row in con.to_dict("records"):
        pid = row.get("프로젝트ID")
        if pid is None or (isinstance(pid, float) and math.isnan(pid)):
            continue
        out.setdefault(str(pid), []).append({
            "status": _s(row.get("계약상태")),
            "firstDate": _s(row.get("최초체결일")),
            "changeDate": _s(row.get("최근변경일")),
            "reinforceDate": _s(row.get("계통보강예정일")),
            "src_doc": _s(row.get("출처문서명")),
        })
    return out


def main() -> None:
    master = pd.read_csv(PROC / "발전소_통합목록.csv", encoding="utf-8-sig")
    history = build_history(pd.read_csv(PROC / "허가이력.csv", encoding="utf-8-sig"))
    contracts = build_contracts(pd.read_csv(PROC / "계통계약.csv", encoding="utf-8-sig"))

    plants = []
    for r in master.to_dict("records"):
        pid = str(r["프로젝트ID"])
        plants.append({
            "id": pid,
            "name": _s(r.get("발전소명")),
            "src": _s(r.get("발전원")),
            "region": _s(r.get("지역")),
            "sgg": _s(r.get("시군구")),
            "operator": _s(r.get("사업주체")),
            "owner": _s(r.get("최대주주")),
            "loc": _s(r.get("허가위치_원문")),
            "mw": _f(r.get("설비용량_MW")),
            "cost": _f(r.get("총사업비_억원")),
            "firstPermit": _s(r.get("최초발전사업허가일")),
            # 허가대장전용 등 회차 없는 사업은 옛 data.json 관례대로 "" (숫자면 float).
            "round": _f(r.get("최근전기위원회회차")) if _f(r.get("최근전기위원회회차")) is not None else "",
            "agendaType": _s(r.get("최근안건유형")),
            "gridCond": _s(r.get("계통보강조건")),
            # 지도 표시용. 옛 data.json 관례대로 소수 3자리로 반올림(약 100m).
            "lat": _round3(r.get("위도")),
            "lon": _round3(r.get("경도")),
            "coordType": _s(r.get("좌표유형")),
            "coordAcc": _s(r.get("좌표정확도")),
            "status": _s(r.get("데이터상태")),
            "src_doc": _s(r.get("대표출처")),
            "src_pg": _s(r.get("출처페이지")),
            "path": _s(r.get("편입경로")),
            "history": history.get(pid, []),
            "contracts": contracts.get(pid, []),
        })

    coords = sum(1 for p in plants if p["lat"] is not None and p["lon"] is not None)
    payload = {"plants": plants,
               "meta": {"기준일": 기준일, "총사업수": len(plants), "좌표있음": coords}}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"data.json 생성: {len(plants)}건 / 좌표 {coords}건 -> {OUT}")


if __name__ == "__main__":
    main()
