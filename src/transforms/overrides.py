"""사용자 원문(허가증 등)·명백한 오류에 대한 확정 보정.

파이프라인이 원본에서 매번 재생성하므로, 사용자 대조로 확정한 내용은 이 모듈에서
매 실행 재적용해야 유지된다. 근거는 지침 §4 최상위 출처(발전사업허가서) 또는
원문 확인으로 판명된 파싱 오류다.
"""

from __future__ import annotations

import re

import pandas as pd


def _nc(s) -> str:
    return re.sub(r"\s+", "", str(s))


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def apply_overrides(master: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """확정 보정을 적용한 (master, 제외된행) 을 돌려준다."""
    m = master.copy()
    removed: list[dict] = []

    # ── 0) 풍력_구분미상 최종 재분류 (좌표·공유수면 근거) ──────────────────
    from transforms.reclassify_wind import reclassify
    unclear = m["발전원"] == "풍력_구분미상"
    for i in m[unclear].index:
        src, basis = reclassify(str(m.at[i, "허가위치_원문"]),
                                str(m.at[i, "허가대장_좌표_원문"]),
                                str(m.at[i, "발전소명"]))
        if src != "풍력_구분미상":
            m.at[i, "발전원"] = src
            m.at[i, "발전원_근거"] = basis

    # ── 1) 안좌쏠라시티 계열: 발전사업허가증으로 용량·명칭 확정 ──────────────
    # 안좌쏠라시티(제2019-25호) 96MW
    mask_solar1 = m["발전소명"].map(
        lambda s: ("안좌스마트팜앤쏠라시티" in _nc(s) or "안좌쏠라시티" in _nc(s))
        and not (_nc(s).endswith("2") or "쏠라시티2" in _nc(s)))
    for i in m[mask_solar1].index:
        m.at[i, "발전소명"] = "안좌쏠라시티 태양광"
        m.at[i, "사업주체"] = "안좌쏠라시티(주)"
        m.at[i, "설비용량_MW"] = 96
        m.at[i, "최초발전사업허가일"] = "2019-01-30"
        m.at[i, "허가위치_원문"] = "전남 신안군 안좌면 내호리 723번지 외 181필지"
        m.at[i, "발전원"] = "태양광"
        m.at[i, "데이터상태"] = "확정"
        m.at[i, "대표출처"] = "발전사업허가증 제2019-25호"

    # 안좌쏠라시티2(제2020-51호) 192MW + 옛명 '안좌스마트팜' 병합
    idx_solar2 = m[m["발전소명"].map(lambda s: "쏠라시티2" in _nc(s))].index
    idx_smartfarm = m[m["발전소명"].map(lambda s: _nc(s) == "안좌스마트팜")].index
    if len(idx_solar2):
        i = idx_solar2[0]
        aliases = str(m.at[i, "발전소명_원문"]) + " | 안좌스마트팜앤쏠라시티2 | 안좌스마트팜"
        m.at[i, "발전소명"] = "안좌쏠라시티2 태양광"
        m.at[i, "사업주체"] = "안좌쏠라시티2(주)"
        m.at[i, "설비용량_MW"] = 192
        m.at[i, "최초발전사업허가일"] = "2019-10-30"
        m.at[i, "허가위치_원문"] = "전남 신안군 안좌면 내호리 및 구대리 일원"
        m.at[i, "발전원"] = "태양광"
        m.at[i, "데이터상태"] = "확정"
        m.at[i, "대표출처"] = "발전사업허가증 제2020-51호"
        m.at[i, "발전소명_원문"] = aliases
    if len(idx_smartfarm):
        removed += m.loc[idx_smartfarm].assign(제외사유="안좌쏠라시티2 옛 법인명(허가증 확인) — 중복 병합").to_dict("records")
        m = m.drop(index=idx_smartfarm)

    # ── 1b) 전남테크노파크 실증단지: 명칭 변천 동일사업 병합 ──────────────
    # 풍력시스템평가센터(6차, 제248차) → 초대형 해상풍력 실증단지(제260·270차).
    # 모두 (재)전남테크노파크 / 영광 백수읍 하사리 3038 공유수면. 제270차의
    # '완도 장보고'로 잘못 이름 붙은 건은 인접 안건 필드 누수로 생긴 유령이므로
    # 이 실증단지에 흡수한다(진짜 완도 장보고=코오롱글로벌/완도 생일면은 별도 유지).
    tp_mask = (m["사업주체"].map(lambda s: "전남테크노파크" in _nc(s))
               & m["시군구"].map(lambda s: _nc(s) == "영광군"))
    tp_idx = list(m[tp_mask].index)
    if len(tp_idx) >= 2:
        keep = tp_idx[0]
        aliases = set()
        latest_mw = 0.0
        for i in tp_idx:
            for a in str(m.at[i, "발전소명_원문"]).split(" | "):
                if a:
                    aliases.add(a)
            try:
                latest_mw = max(latest_mw, float(m.at[i, "설비용량_MW"]))
            except (TypeError, ValueError):
                pass
        m.at[keep, "발전소명"] = "초대형 해상풍력 실증단지"
        m.at[keep, "발전소명_원문"] = " | ".join(sorted(aliases))
        m.at[keep, "설비용량_MW"] = latest_mw
        m.at[keep, "허가위치_원문"] = "전남 영광군 백수읍 하사리 3038 외 공유수면 일원"
        for i in tp_idx[1:]:
            removed.append({**m.loc[i].to_dict(),
                            "제외사유": "전남테크노파크 실증단지 명칭변천 동일사업 병합"})
        m = m.drop(index=tp_idx[1:])

    # ── 1c) 표기차로 안 묶인 동일사업 병합 (원문 대조 확인) ────────────────
    def merge_same(name_filter, mw, keep_name, why):
        idx = list(m[m["발전소명"].map(name_filter)].index)
        idx = [i for i in idx if abs(_to_float(m.at[i, "설비용량_MW"]) - mw) < 0.5]
        if len(idx) >= 2:
            keep = idx[0]
            aliases = set()
            for i in idx:
                aliases.update(a for a in str(m.at[i, "발전소명_원문"]).split(" | ") if a)
            m.at[keep, "발전소명"] = keep_name
            m.at[keep, "발전소명_원문"] = " | ".join(sorted(aliases))
            for i in idx[1:]:
                removed.append({**m.loc[i].to_dict(), "제외사유": why})
            return idx[1:]
        return []

    drop_idx = []
    # 해밀 고흥솔라팜: ㈜/(주) 표기차로 분리된 동일사업(에이제이해밀솔라 90MW)
    drop_idx += merge_same(lambda s: "해밀고흥솔라팜" in _nc(s), 90.0,
                           "해밀 고흥솔라팜 태양광", "㈜/(주) 표기차 동일사업 병합")
    # 완도 금일해상풍력 2단계: 위치표기차로 분리된 동일사업(400MW)
    drop_idx += merge_same(lambda s: "완도금일해상풍력2단계" in _nc(s) or
                           ("금일" in _nc(s) and "2단계" in _nc(s)), 400.0,
                           "완도 금일해상풍력 2단계", "위치표기차 동일사업 병합")
    # 여수 삼산 5단지: 안건명이 '여수삼산해상풍력(주)의 …'/'여수 삼산 …' 두 표기로
    # 갈려 미병합(둘 다 360MW·여수·동일 사업명 핵심). 사업자명 접두만 차이.
    drop_idx += merge_same(lambda s: "여수삼산해상풍력5단지" in _nc(s), 360.0,
                           "여수 삼산 해상풍력 5단지", "사업자명 접두 표기차 동일사업 병합")
    # 여수 초도: '초도해상풍력(주)의 …'/'여수 초도 …' 두 표기(둘 다 240MW). 2차(168MW)는 별개.
    drop_idx += merge_same(lambda s: "여수초도해상풍력" in _nc(s), 240.0,
                           "여수 초도 해상풍력", "사업자명 접두 표기차 동일사업 병합")
    # SK E&S 전남 해상풍력 2·3단계: '에스케이이엔에스(주)의 …'/'전남 해상풍력 …' 두
    # 표기로 갈림(단계별 각 399MW·신안). 사업자명 접두만 차이 — 단계별 동일사업 병합.
    drop_idx += merge_same(lambda s: "전남해상풍력2단계" in _nc(s), 399.0,
                           "전남 해상풍력 2단계", "사업자명 접두 표기차 동일사업 병합")
    drop_idx += merge_same(lambda s: "전남해상풍력3단계" in _nc(s), 399.0,
                           "전남 해상풍력 3단계", "사업자명 접두 표기차 동일사업 병합")
    if drop_idx:
        m = m.drop(index=drop_idx)

    # ── 2) 타지역 사업(회의록 2단 파싱 위치 오귀속) 제외 ──────────────────
    # 평창 봉진풍력(강원 평창), 하늘농장 경주4호(경북 경주). 위치칸에 인접 고흥
    # 안건 주소가 잘못 붙었음. 원문 확인 결과 전남 사업 아님.
    mask_foreign = m["발전소명"].str.contains("평창 봉진|경주4호|경주 4호", na=False)
    if mask_foreign.any():
        removed += m[mask_foreign].assign(
            제외사유="타지역 사업(위치 오귀속) — 원문 확인 결과 전남 아님").to_dict("records")
        m = m[~mask_foreign]

    m = m.reset_index(drop=True)
    m["설비용량_MW"] = pd.to_numeric(m["설비용량_MW"], errors="coerce")
    return m, removed
