r"""발전소 통합목록에 좌표를 채운다 (지침 §15).

실행: .venv\Scripts\python.exe tools\resolve_coordinates.py

  해상풍력 : 허가대장 사업구역 좌표(폴리곤/점) 우선. 육상 주소를 발전기 위치로 쓰지 않는다.
             사업구역 좌표가 없으면 '좌표없음'(임의로 바다에 찍지 않는다).
  육상     : VWorld 지번→도로명→읍면동 순 지오코딩.

지오코딩 결과는 캐시(data/interim/geocode_cache.json)에 저장해 재실행 시 재호출을 피한다.
좌표는 원본(WGS84 위경도)을 그대로 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from transforms.coordinates import resolve_offshore                       # noqa: E402
from transforms.vworld_geocode import VWorldGeocoder                      # noqa: E402

MASTER = ROOT / "data/processed/발전소_통합목록.csv"
LEDGER = ROOT / "data/interim/허가대장_전남광주_정규화.csv"
CACHE = ROOT / "data/interim/geocode_cache.json"


def offshore_coord_by_project(ledger: pd.DataFrame) -> dict:
    """프로젝트별 최선의 해상 사업구역 좌표. 폴리곤 점수가 많은 것을 채택한다."""
    from transforms.dedup import name_core

    best: dict[str, dict] = {}
    for row in ledger.to_dict("records"):
        raw = str(row.get("좌표_원문") or "")
        if not raw.strip():
            continue
        result = resolve_offshore(raw)
        if result.위도 is None:
            continue
        key = (name_core(str(row.get("사업자") or "")), row.get("시군구") or "")
        prev = best.get(key)
        if prev is None or len(result.폴리곤) > prev["_점수"]:
            best[key] = {"위도": result.위도, "경도": result.경도,
                         "좌표유형": result.좌표유형, "좌표정확도": result.좌표정확도,
                         "판정근거": result.판정근거, "폴리곤점수": len(result.폴리곤),
                         "_점수": len(result.폴리곤)}
    return best


def main() -> None:
    master = pd.read_csv(MASTER, encoding="utf-8-sig").where(lambda d: d.notna(), None)
    ledger = pd.read_csv(LEDGER, encoding="utf-8-sig").where(lambda d: d.notna(), None)

    from transforms.dedup import name_core
    offshore = offshore_coord_by_project(ledger)

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    geocoder = None                          # 육상 사업이 있을 때만 생성 (키 필요)

    rows, stats = [], {"사업구역": 0, "지번": 0, "도로명": 0, "읍면동대표": 0, "좌표없음": 0}
    for record in master.to_dict("records"):
        source = record.get("발전원")
        coord = None

        if source == "해상풍력":
            key = f"{name_core(str(record.get('사업주체') or ''))}|{record.get('시군구') or ''}"
            hit = offshore.get((name_core(str(record.get('사업주체') or '')),
                                record.get("시군구") or ""))
            if hit:
                coord = {**hit}
            else:
                coord = {"위도": None, "경도": None, "좌표유형": "좌표없음",
                         "좌표정확도": "좌표없음",
                         "판정근거": "해상 사업구역 좌표 없음 — 임의 배치 안 함"}
        else:
            address = str(record.get("허가위치_원문") or "")
            if address in cache:
                coord = cache[address]
            elif address.strip():
                if geocoder is None:
                    geocoder = VWorldGeocoder()
                coord = geocoder.geocode(address)
                cache[address] = coord
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            else:
                coord = {"위도": None, "경도": None, "좌표유형": "좌표없음",
                         "좌표정확도": "좌표없음", "판정근거": "허가위치 원문 없음"}

        record.update({
            "위도": coord.get("위도"), "경도": coord.get("경도"),
            "좌표유형": coord.get("좌표유형"), "좌표정확도": coord.get("좌표정확도"),
            "좌표판정근거": coord.get("판정근거", ""),
        })
        bucket = ("사업구역" if str(coord.get("좌표유형", "")).startswith("사업구역")
                  else coord.get("좌표유형", "좌표없음"))
        stats[bucket] = stats.get(bucket, 0) + 1
        rows.append(record)

    out = pd.DataFrame(rows)
    out.to_csv(MASTER, index=False, encoding="utf-8-sig")
    have = out["위도"].notna().sum()
    print(f"좌표 확정 {have}/{len(out)}건")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    off = out[out["발전원"] == "해상풍력"]
    print(f"\n해상풍력 {len(off)}건 중 좌표 있음 {off['위도'].notna().sum()}건 "
          f"(사업구역 좌표만 사용, 육상주소 미사용)")
    land = out[out["발전원"].isin(["태양광", "육상풍력"])]
    print(f"육상 {len(land)}건 중 좌표 있음 {land['위도'].notna().sum()}건")


if __name__ == "__main__":
    main()
