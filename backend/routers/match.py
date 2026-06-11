"""
AI 매칭 엔진 라우터
POST /api/match → Gemini 임베딩 + FAISS → 상위 5개 공단 추천
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
            "name": park.get("name", ""),
            "region": park.get("region", ""),
            "city": park.get("city", ""),
            "score": r["score"],
            "available_area": park.get("available_area", 0),
            "vacancy_rate": park.get("vacancy_rate", 0),
            "rent_per_sqm": park.get("rent_per_sqm", 0),
            "industries": park.get("industries", []),
            "logistics": park.get("logistics", []),
            "features": park.get("features", []),
            "description": park.get("description", ""),
            "subsidy": park.get("subsidy", ""),
            "contact": park.get("contact", ""),
            "website": park.get("website", ""),
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
