"""
AI 매칭 엔진 라우터
POST /api/match → Groq LLM(openai/gpt-oss-120b)이 기업 조건과 공단 데이터를 분석 → 상위 5개 공단 추천
"""
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db, MatchingHistory
from services.embedding import get_embedding_service

router = APIRouter(prefix="/api", tags=["matching"])

# 단지 유형별 인허가 절차 (산업입지법 등에 근거한 일반적 구분 — 단지별 세부 스펙은 아님)
# {city}는 build_infra_note()에서 해당 공단의 실제 관할 지자체명(시·군·구)으로 치환된다 —
# 예전엔 "관할 지자체(시·군·구)"라는 문구 그대로 노출되어 정작 어디인지 알 수 없었음
TYPE_NOTES = {
    "국가산단": "국가산업단지 — 산업통상자원부 지정, 한국산업단지공단(KICOX) 등 관리기관의 입주 심사·인허가 절차를 따릅니다.",
    "일반산단": "일반산업단지 — {city}{josa_ga} 지정·관리하며, 인허가는 지자체 산업단지 관리기관을 통해 진행됩니다.",
    "도시첨단산단": "도시첨단산업단지 — {city} 지정, IT·지식서비스 등 첨단업종 중심으로 조성되어 입주 업종 제한이 있을 수 있습니다.",
    "농공산단": "농공단지 — {city} 지정, 농어촌 지역 소규모 제조업 중심으로 조성되어 부지·임대료가 저렴하나 대규모 부지·물류 인프라는 제한적일 수 있습니다.",
    "자유무역지역": "자유무역지역 — 산업통상자원부 산하 특례 지역으로 관세 유예 등 통관 인센티브가 있으며, 별도 관리기관의 입주 승인이 필요합니다.",
}

# 업종별 일반적으로 확인이 필요한 인프라 항목 (공단별 실측 스펙이 아닌, 업종 특성상 체크리스트)
INDUSTRY_INFRA_HINTS = [
    ({"석유화학", "화학", "화학소재", "정유", "플라스틱"}, "폐수·폐기물 처리 시설과 위험물 취급 인허가 여건을 확인하세요."),
    ({"반도체", "반도체부품", "전자", "전기전자", "디스플레이", "IT부품", "모바일"}, "안정적인 전력·용수 공급 능력과 클린룸 인프라 여부를 확인하세요."),
    ({"식품", "바이오", "바이오·제약", "의료기기", "화장품"}, "위생 등급 인증, 냉동·냉장 물류, 상수도 수질 기준을 확인하세요."),
    ({"자동차", "자동차부품", "조선", "조선기자재", "항공부품", "기계", "기계부품", "금속", "금속가공", "철강"}, "대형 화물 운송로와 배후 협력업체 접근성을 확인하세요."),
    ({"물류", "수출가공"}, "고속도로 IC·항만·공항 접근성을 우선 확인하세요."),
]


def _josa_i_ga(word: str) -> str:
    """받침 유무에 따라 '이'/'가' 중 알맞은 주격 조사를 고른다 (예: 가평군이, 안성시가)."""
    if not word:
        return "가"
    code = ord(word[-1]) - 0xAC00
    if 0 <= code <= 11171:
        return "가" if code % 28 == 0 else "이"
    return "가"


def build_infra_note(park: dict) -> str:
    """단지 유형·업종 기준의 일반적 확인사항. 공단별 실측 인프라 스펙(전력 용량 등)이 아니라
    입지 선정 시 놓치기 쉬운 체크포인트를 안내하는 용도."""
    notes = []
    dev_status = park.get("dev_status")
    if dev_status == "조성중":
        notes.append("⚠️ 현재 조성 중인 단지입니다 — 즉시 입주가 아닌 향후 입주 일정 확인이 필요합니다.")
    elif dev_status == "미개발":
        notes.append("⚠️ 아직 미개발 상태인 단지입니다 — 실제 조성·분양 일정을 관리기관에 반드시 확인하세요.")
    type_note_tmpl = TYPE_NOTES.get(park.get("type", ""))
    if type_note_tmpl:
        city = park.get("city") or park.get("region") or "관할 지자체"
        notes.append(type_note_tmpl.format(city=city, josa_ga=_josa_i_ga(city)))
    park_industries = set(park.get("industries") or [])
    matched = []
    for keys, hint in INDUSTRY_INFRA_HINTS:
        if park_industries & keys and hint not in matched:
            matched.append(hint)
    notes.extend(matched[:2])  # 카드가 너무 길어지지 않도록 업종 힌트는 최대 2개까지만
    return " ".join(notes)


class MatchRequest(BaseModel):
    industry: str
    size: str
    area: str
    region: Optional[str] = ""
    budget: Optional[str] = ""
    logistics: Optional[str] = ""
    extra: Optional[str] = ""
    company_name: Optional[str] = ""


class MatchResult(BaseModel):
    rank: int
    name: str
    region: str
    city: str
    score: float
    reason: str = ""
    breakdown: dict = {}
    dev_status: str = "완료"
    infra_note: str = ""
    available_area: float
    vacancy_rate: float
    rent_per_sqm: int
    industries: list
    logistics: list
    features: list
    description: str
    subsidy: str
    contact: str
    website: str
    lat: float
    lng: float


@router.post("/match")
async def run_match(req: MatchRequest, db: Session = Depends(get_db)):
    """기업 조건 입력 → AI 매칭 → 상위 5개 공단 추천"""
    svc = get_embedding_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="AI 매칭 엔진이 초기화되지 않았습니다. 잠시 후 다시 시도해주세요.")

    try:
        results = svc.search(
            industry=req.industry,
            size=req.size,
            area=req.area,
            region=req.region or "",
            budget=req.budget or "",
            logistics=req.logistics or "",
            extra=req.extra or "",
            top_k=5,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"매칭 오류: {str(e)}")

    # 결과 포맷팅
    formatted = []
    for i, r in enumerate(results):
        park = r["park"]
        formatted.append({
            "rank": i + 1,
            "id": park.get("id"),
            "name": park.get("name", ""),
            "region": park.get("region", ""),
            "city": park.get("city", ""),
            "score": r["score"],
            "reason": r.get("reason", ""),
            "breakdown": r.get("breakdown", {}),
            "dev_status": park.get("dev_status") or "완료",
            "infra_note": build_infra_note(park),
            "available_area": park.get("available_area", 0),
            "vacancy_rate": park.get("vacancy_rate", 0),
            "rent_per_sqm": park.get("rent_per_sqm", 0),
            "industries": park.get("industries", []),
            "logistics": park.get("logistics", []),
            "features": park.get("features", []),
            # park.get(key, "")는 키가 아예 없을 때만 기본값을 주므로, 값이 JSON null인
            # 원본 데이터(1,406개 중 상당수가 description/subsidy/contact/website 미확보)에는
            # 소용없다 — "or \"\""로 None을 명시적으로 걸러내야 프론트에 "null" 문자열이 안 새어나간다
            "description": park.get("description") or "",
            "subsidy": park.get("subsidy") or "",
            "contact": park.get("contact") or "",
            "website": park.get("website") or "",
            "lat": park.get("lat", 0),
            "lng": park.get("lng", 0),
        })

    # 매칭 이력 저장
    try:
        history = MatchingHistory(
            company_name=req.company_name or "익명",
            industry=req.industry,
            size=req.size,
            area=req.area,
            region=req.region or "",
            budget=req.budget or "",
            logistics=req.logistics or "",
            extra=req.extra or "",
            matched_parks=json.dumps([r["name"] for r in formatted], ensure_ascii=False),
            status="매칭 완료",
            created_at=datetime.utcnow(),
        )
        db.add(history)
        db.commit()
    except Exception:
        pass  # 이력 저장 실패해도 결과는 반환

    return {"results": formatted, "total": len(formatted)}
