"""
대시보드 API 라우터
공실 현황, 매칭 통계, 최근 매칭 이력 제공
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta

from database import get_db, IndustrialPark, MatchingHistory

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard/stats")
def get_stats(db: Session = Depends(get_db)):
    """대시보드 핵심 통계"""
    parks = db.query(IndustrialPark).all()

    total_available = sum(p.available_area or 0 for p in parks)
    avg_vacancy = (sum(p.vacancy_rate or 0 for p in parks) / len(parks)) if parks else 0

    # 이달 매칭 건수
    this_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    monthly_matches = db.query(MatchingHistory).filter(
        MatchingHistory.created_at >= this_month
    ).count()

    # 전체 등록 기업 수 (매칭 이력 기준)
    total_companies = db.query(MatchingHistory).count()

    return {
        "total_available_area": f"{total_available/10000:.0f}만㎡" if total_available >= 10000 else f"{total_available:,.0f}㎡",
        "total_available_area_raw": total_available,
        "avg_vacancy_rate": round(avg_vacancy, 1),
        "monthly_matches": monthly_matches,
        "total_companies": total_companies,
        "avg_search_days": 12,  # 플랫폼 평균 탐색 기간
        "total_parks": len(parks),
    }


@router.get("/dashboard/parks")
def get_parks(db: Session = Depends(get_db)):
    """산업단지 공실 현황 목록"""
    parks = db.query(IndustrialPark).order_by(IndustrialPark.monthly_inquiries.desc()).all()

    result = []
    for p in parks:
        # 공실률에 따른 상태 분류
        if p.vacancy_rate and p.vacancy_rate >= 25:
            status = "주의"
            status_class = "high"
        elif p.vacancy_rate and p.vacancy_rate >= 15:
            status = "보통"
            status_class = "mid"
        else:
            status = "여유"
            status_class = "low"

        # 공실률 색상
        if p.vacancy_rate and p.vacancy_rate >= 25:
            bar_color = "#E24B4A"
        elif p.vacancy_rate and p.vacancy_rate >= 15:
            bar_color = "#EF9F27"
        else:
            bar_color = "#639922"

        result.append({
            "id": p.id,
            "name": p.name,
            "city": p.city,
            "region": p.region,
            "vacancy_rate": p.vacancy_rate or 0,
            "available_area": f"{p.available_area:,.0f}㎡" if p.available_area else "0㎡",
            "available_area_raw": p.available_area or 0,
            "rent_per_sqm": f"{p.rent_per_sqm:,}원/㎡" if p.rent_per_sqm else "0원/㎡",
            "industries": json.loads(p.industries) if p.industries else [],
            "status": status,
            "status_class": status_class,
            "bar_color": bar_color,
            "monthly_inquiries": p.monthly_inquiries or 0,
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
        })

    return {"parks": result, "total": len(result)}


@router.get("/dashboard/recent-matches")
def get_recent_matches(limit: int = 10, db: Session = Depends(get_db)):
    """최근 매칭 이력"""
    histories = db.query(MatchingHistory).order_by(
        desc(MatchingHistory.created_at)
    ).limit(limit).all()

    result = []
    for h in histories:
        matched = []
        try:
            matched = json.loads(h.matched_parks) if h.matched_parks else []
        except Exception:
            pass

        result.append({
            "id": h.id,
            "company_name": h.company_name or "익명",
            "industry": h.industry,
            "size": h.size,
            "matched_park": matched[0] if matched else "",
            "status": h.status,
            "created_at": h.created_at.isoformat() if h.created_at else "",
        })

    return {"matches": result, "total": len(result)}


@router.get("/parks")
def list_parks(
    region: str = "",
    industry: str = "",
    db: Session = Depends(get_db)
):
    """산업단지 목록 조회 (필터링 지원)"""
    query = db.query(IndustrialPark)

    if region:
        query = query.filter(IndustrialPark.region.contains(region))

    parks = query.all()

    result = []
    for p in parks:
        industries = json.loads(p.industries) if p.industries else []

        # 업종 필터
        if industry and not any(industry in ind for ind in industries):
            continue

        result.append({
            "id": p.id,
            "name": p.name,
            "city": p.city,
            "region": p.region,
            "type": p.type,
            "available_area": p.available_area or 0,
            "vacancy_rate": p.vacancy_rate or 0,
            "rent_per_sqm": p.rent_per_sqm or 0,
            "industries": industries,
            "features": json.loads(p.features) if p.features else [],
            "subsidy": p.subsidy or "",
            "lat": p.lat or 0,
            "lng": p.lng or 0,
        })

    return {"parks": result, "total": len(result)}
