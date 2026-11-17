"""시·군·구·시·도 기업·투자유치 지원 조례의 지원 조항 원문(scripts/fetch_local_subsidy.py, 자치법규정보시스템).
AI 상담(rag.py)과 AI 매칭 결과 카드(routers/match.py)가 같이 쓴다. 원문은 고쳐 쓰지 않는다."""
import json
import os
import re
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    with open(os.path.join(BASE_DIR, "data", "local_subsidy.json"), encoding="utf-8") as _f:
        _DATA = json.load(_f)
    LOCAL: Dict[str, dict] = _DATA.get("local", {})
    COLLECTED_AT: str = _DATA.get("collected_at", "")
except (OSError, ValueError):
    LOCAL, COLLECTED_AT = {}, ""

SIDO_FULL = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "광주광역시",
             "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
             "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
             "경남": "경상남도", "제주": "제주특별자치도"}

_HEAD = re.compile(r"(제\d+조(?:의\d+)?)\(([^)]*)\)")


def key_for(region: str, city: str) -> Optional[str]:
    """단지의 시·도·시·군으로 조례 데이터 키를 찾는다. 시·군·구 자체 지원 조항이 없으면 시·도 본청 조례."""
    city = (city or "").split(" ")[0]
    key = f"{region}|{city}"
    if key in LOCAL:
        return key
    if f"{region}|본청" in LOCAL:
        return f"{region}|본청"
    if region in ("세종", "제주"):  # 시·군 조례가 따로 없는 곳은 수집 때 본청 기준으로 들어가 있다
        return next((k for k in LOCAL if k.startswith(region + "|")), None)
    return None


def scope_name(key: str) -> str:
    """화면·답변에 쓸 이름: "김해시" 또는 "경기도(시·도 조례)"."""
    rec = LOCAL.get(key) or {}
    if rec.get("city") == "본청":
        return SIDO_FULL.get(rec.get("region"), rec.get("region", ""))
    return rec.get("city") or rec.get("region", "")


def summary(region: str, city: str, max_ords: int = 3, max_items: int = 8) -> Optional[dict]:
    """매칭 카드용 요약: 조례명·시행일·담당 부서와 지원 조항 제목(예: "제6조 고용보조금")만. 원문은 articles()로."""
    key = key_for(region, city)
    if not key:
        return None
    rec = LOCAL[key]
    ords = []
    for o in rec["ordinances"][:max_ords]:
        items = []
        for a in o["articles"]:
            m = _HEAD.match(a)
            if m and m.group(2) not in items:
                items.append(m.group(2))
        ords.append({"title": o["title"], "effective": o.get("effective", ""), "dept": o.get("dept", ""),
                     "phone": o.get("phone", ""), "items": items[:max_items], "count": len(o["articles"])})
    return {"key": key, "scope": "시·도" if rec.get("city") == "본청" else "시·군·구", "name": scope_name(key),
            "ordinances": ords, "collected_at": COLLECTED_AT,
            "source": "자치법규정보시스템(elis.go.kr) 현행 자치법규"}


def articles(key: str) -> Optional[dict]:
    """조례별 지원 조항 원문 전체(모달 표시용)."""
    rec = LOCAL.get(key)
    if not rec:
        return None
    return {"key": key, "name": scope_name(key), "scope": "시·도" if rec.get("city") == "본청" else "시·군·구",
            "ordinances": rec["ordinances"], "collected_at": COLLECTED_AT,
            "source": "자치법규정보시스템(elis.go.kr) 현행 자치법규"}
