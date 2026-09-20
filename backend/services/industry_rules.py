"""
공식 고시문서에서 확인한 단지별 입주 가능/제한 업종을 매칭에 반영한다.

data/park_industry_rules.json 에 단지 id별로 입주대상 업종(allowed) 또는 제한업종(restricted)이
한국표준산업분류 중분류 코드로 들어 있다. 사용자가 고른 업종을 중분류로 바꿔서 비교하고,
- 입주 불가로 확인된 단지는 추천에서 제외한다(헛걸음 방지)
- 입주 가능(또는 일부 세부업종만 가능)이 확인된 단지에는 근거 문구를 붙인다
문서에서 확인하지 못한 단지는 규칙이 없으므로 아무 영향도 주지 않는다(추측 금지).
"""
import json
import os
from functools import lru_cache
from typing import Dict, Optional, Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(_BASE, "data", "park_industry_rules.json")

# AI 매칭 폼(SiteMatchAI.html #f-industry)의 업종 선택값 → 중분류 코드
FORM_INDUSTRY_TO_DIVISION: Dict[str, int] = {
    "식료품 제조업": 10, "음료 제조업": 11, "담배 제조업": 12, "섬유제품 제조업": 13,
    "의복·모피제품 제조업": 14, "가죽·가방·신발 제조업": 15, "목재·나무제품 제조업": 16,
    "펄프·종이 제조업": 17, "인쇄업": 18, "석유정제품 제조업": 19, "화학물질·화학제품 제조업": 20,
    "의약품 제조업": 21, "고무·플라스틱제품 제조업": 22, "비금속광물제품 제조업": 23,
    "1차 금속 제조업": 24, "금속가공제품 제조업": 25, "전자부품·통신장비 제조업": 26,
    "의료·정밀·광학기기 제조업": 27, "전기장비 제조업": 28, "기타 기계·장비 제조업": 29,
    "자동차·트레일러 제조업": 30, "기타 운송장비 제조업": 31, "가구 제조업": 32,
    "기타 제품 제조업": 33, "산업용 기계·장비 수리업": 34,
}

# 챗봇처럼 자유 문장으로 업종이 들어올 때 쓰는 키워드 → 중분류 (여러 개가 걸리면 모호하므로 적용 안 함)
_KEYWORD_TO_DIVISION = (
    (("식품", "식료"), 10), (("음료",), 11), (("섬유",), 13), (("의복",), 14), (("가죽", "신발"), 15),
    (("목재",), 16), (("종이", "펄프"), 17), (("인쇄",), 18), (("석유정제", "코크스"), 19),
    (("화학",), 20), (("의약", "바이오"), 21), (("고무", "플라스틱"), 22),
    (("비금속", "시멘트", "유리"), 23), (("1차금속", "철강", "제철"), 24), (("금속가공",), 25),
    (("전자", "반도체", "디스플레이", "통신장비"), 26), (("의료기기", "정밀", "광학"), 27),
    (("전기장비", "배터리", "이차전지"), 28), (("기계",), 29), (("자동차",), 30),
    (("운송장비", "조선", "선박"), 31), (("가구",), 32),
)


@lru_cache(maxsize=1)
def _load_rules() -> Dict[str, dict]:
    if not os.path.isfile(RULES_PATH):
        return {}
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("rules", {})


def guess_division(industry_text: Optional[str]) -> Optional[int]:
    """업종 입력(폼 선택값 또는 자유 문장)을 중분류 코드로 바꾼다. 확실하지 않으면 None."""
    if not industry_text:
        return None
    text = industry_text.strip()
    if text in FORM_INDUSTRY_TO_DIVISION:
        return FORM_INDUSTRY_TO_DIVISION[text]
    compact = text.replace(" ", "")
    hits = {div for kws, div in _KEYWORD_TO_DIVISION if any(k in compact for k in kws)}
    return hits.pop() if len(hits) == 1 else None


def check(park_id, division: Optional[int]) -> Tuple[str, str]:
    """(상태, 근거 문구) — 상태는 "blocked"(입주 불가 확인) / "allowed"(입주 대상 확인) /
    "partial"(일부 세부업종만 가능) / "unknown"(문서에서 확인 안 됨)."""
    rule = _load_rules().get(str(park_id))
    if not rule or division is None:
        return "unknown", ""
    src = rule.get("source", "공식 고시문서")
    code = str(division)
    partial = (rule.get("partial") or {}).get(code)
    if "allowed" in rule:
        if division not in rule["allowed"]:
            return "blocked", f"공식 고시({src})상 입주대상 업종이 아님"
    elif division in rule.get("restricted", []):
        return "blocked", f"공식 고시({src})상 입주제한 업종"
    if partial:
        return "partial", f"공식 고시({src}): {partial}"
    if "allowed" in rule:
        return "allowed", f"공식 고시({src})상 입주대상 업종으로 확인됨"
    return "unknown", ""


def is_blocked(park_id, division: Optional[int]) -> bool:
    return check(park_id, division)[0] == "blocked"
