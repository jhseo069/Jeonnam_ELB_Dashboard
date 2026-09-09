"""사용자가 원문 대조로 확정한 OCR 보정을 회의록 안건에 적용한다.

입력: data/interim/OCR_보정규칙.json  (reports/OCR_대조확인_최종.xlsx 에서 파생)
대상: data/extracted/회의록_안건_OCR.csv 의 해당 (회차, 안건명) 행

보정된 값은 사람이 원문과 대조해 확정한 것이므로 데이터상태를 '확정'으로 올린다
(지침 §17.3: 공식 원문 확인 → 확정). 보정 이력은 별도 칼럼에 남긴다.

'사업주체'에 화살표(→)가 있으면 양수인가/주식취득인가의 양도·양수 관계이므로
양도인·양수인으로 분리하고, 현재 사업주체는 양수인으로 둔다(지침 §13).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ARROW = re.compile(r"\s*(?:→|->|⇒|=>)\s*")


def _match_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def split_transfer(value: str) -> tuple[str, str, str]:
    """'㈜한화 → 한화오션㈜' -> (양도인, 양수인, 현재사업주체=양수인)."""
    parts = ARROW.split(value)
    if len(parts) == 2:
        transferor, transferee = parts[0].strip(), parts[1].strip()
        return transferor, transferee, transferee
    return "", "", value.strip()


def apply_corrections(agenda_df, rules_path: Path):
    """회의록 OCR 안건 DataFrame 에 보정을 적용한 새 DataFrame 을 돌려준다."""
    if not rules_path.exists():
        agenda_df["보정메모"] = ""
        return agenda_df

    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    df = agenda_df.copy()
    if "보정메모" not in df.columns:
        df["보정메모"] = ""

    for rule in rules:
        round_no = rule["회차"]
        target_name = _match_key(rule["안건명"])
        fixes = rule["보정"]

        mask = (df["회차"].astype("Int64").astype(str) == str(round_no)) & \
               (df["안건명"].map(_match_key) == target_name)
        if not mask.any():
            # 안건명이 보정으로 바뀔 예정이면 치환 전 이름으로 다시 찾는다
            replace = fixes.get("_치환_안건명")
            if replace:
                wrong, _right = replace
                mask = (df["회차"].astype("Int64").astype(str) == str(round_no)) & \
                       (df["안건명"].str.contains(wrong, na=False))
        if not mask.any():
            continue

        for field, value in fixes.items():
            if field == "_치환_안건명":
                wrong, right = value
                df.loc[mask, "안건명"] = df.loc[mask, "안건명"].str.replace(wrong, right, regex=False)
                df.loc[mask, "보정메모"] += f"[안건명 {wrong}→{right}]"
                continue

            if field == "사업주체" and ARROW.search(value):
                transferor, transferee, current = split_transfer(value)
                df.loc[mask, "사업주체"] = current
                df.loc[mask, "양도인"] = transferor
                df.loc[mask, "양수인"] = transferee
                df.loc[mask, "보정메모"] += f"[사업주체 확정: {transferor}→{transferee}]"
            elif field == "설비용량":
                num = re.search(r"([\d,]+(?:\.\d+)?)", value)
                df.loc[mask, "설비용량_원문"] = value
                if num:
                    df.loc[mask, "설비용량_MW"] = float(num.group(1).replace(",", ""))
                df.loc[mask, "보정메모"] += f"[설비용량 확정: {value}]"
            elif field == "총사업비":
                num = re.search(r"([\d,]+(?:\.\d+)?)", value)
                df.loc[mask, "총사업비_원문"] = value
                if num:
                    df.loc[mask, "총사업비_억원"] = float(num.group(1).replace(",", ""))
                df.loc[mask, "보정메모"] += f"[총사업비 확정: {value}]"
            else:
                column = {"최대주주": "최대주주", "사업주체": "사업주체"}.get(field, field)
                if column in df.columns:
                    df.loc[mask, column] = value
                    df.loc[mask, "보정메모"] += f"[{column} 확정: {value}]"

        df.loc[mask, "추출방식"] = "OCR+원문확정"

    return df
