"""
대시보드 API 라우터
공실 현황, 매칭 통계, 최근 매칭 이력 제공
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Dict, Optional

from database import get_db, IndustrialPark, MatchingHistory, VacancySnapshot

router = APIRouter(prefix="/api", tags=["dashboard"])

# 매칭 이력의 실제 진행 상태 — 공단 담당자가 대시보드에서 직접 갱신
MATCH_STATUS_OPTIONS = ["매칭 완료", "현장 방문 예약", "입주 확정", "보류"]


class MatchStatusUpdate(BaseModel):
    status: str


def require_access(x_access_key: Optional[str] = Header(default=None)) -> str:
    """대시보드(관공서·관리자 전용) 접근 검증.
    ADMIN_KEY와 일치하면 "admin", GOV_KEY와 일치하면 "gov" 역할을 반환한다.
    두 키 중 하나라도 서버에 설정돼 있지 않으면(빈 값) 해당 역할로는 접근을 허용하지 않는다 —
    환경변수 누락이 곧 "누구나 통과"로 이어지는 실수를 막기 위함."""
    admin_key = os.getenv("ADMIN_KEY") or ""
    gov_key = os.getenv("GOV_KEY") or ""
    if x_access_key and admin_key and x_access_key == admin_key:
        return "admin"
    if x_access_key and gov_key and x_access_key == gov_key:
        return "gov"
    raise HTTPException(status_code=401, detail="접근 키가 올바르지 않습니다.")


def require_admin(role: str = Depends(require_access)) -> str:
    """관리자(SiteMatch 운영진) 전용 엔드포인트 보호."""
    if role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return role


def _ensure_today_snapshot(db: Session, avg_vacancy: float, total_available: float) -> None:
    """오늘 날짜의 공실 스냅샷이 없으면 하나 기록한다 (추이 차트용 시계열 적재)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    exists = db.query(VacancySnapshot).filter(VacancySnapshot.snapshot_date == today).first()
    if exists:
        return
    try:
        db.add(VacancySnapshot(
            snapshot_date=today,
            avg_vacancy_rate=round(avg_vacancy, 1),
            total_available_area=total_available,
        ))
        db.commit()
    except Exception:
        db.rollback()  # 동시 요청으로 인한 unique 충돌 등은 무시


@router.get("/dashboard/stats")
def get_stats(db: Session = Depends(get_db), role: str = Depends(require_access)):
    """대시보드 핵심 통계 (관공서·관리자 전용)"""
    parks = db.query(IndustrialPark).all()

    total_available = sum(p.available_area or 0 for p in parks)
    avg_vacancy = (sum(p.vacancy_rate or 0 for p in parks) / len(parks)) if parks else 0

    _ensure_today_snapshot(db, avg_vacancy, total_available)

    # 이달 매칭 건수
    this_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    monthly_matches = db.query(MatchingHistory).filter(
        MatchingHistory.created_at >= this_month
    ).count()

    # 전체 등록 기업 수 (매칭 이력 기준)
    total_companies = db.query(MatchingHistory).count()

    # 실제 입주 확정 건수 (공단 담당자가 상태를 갱신한 건만 집계)
    confirmed_matches = db.query(MatchingHistory).filter(
        MatchingHistory.status == "입주 확정"
    ).count()

    return {
        "total_available_area": f"{total_available/10000:.0f}만㎡" if total_available >= 10000 else f"{total_available:,.0f}㎡",
        "total_available_area_raw": total_available,
        "avg_vacancy_rate": round(avg_vacancy, 1),
        "monthly_matches": monthly_matches,
        "total_companies": total_companies,
        "confirmed_matches": confirmed_matches,
        "avg_search_days": 12,  # 플랫폼 평균 탐색 기간
        "total_parks": len(parks),
    }


@router.get("/dashboard/vacancy-trend")
def get_vacancy_trend(days: int = 30, db: Session = Depends(get_db), role: str = Depends(require_access)):
    """공실률 추이 (일별 스냅샷, 관공서·관리자 전용). 서비스 사용일마다 하루 1포인트씩 쌓인다."""
    snapshots = db.query(VacancySnapshot).order_by(
        VacancySnapshot.snapshot_date.asc()
    ).limit(days).all()

    return {
        "points": [
            {
                "date": s.snapshot_date,
                "avg_vacancy_rate": s.avg_vacancy_rate,
                "total_available_area": s.total_available_area,
            }
            for s in snapshots
        ],
        "total": len(snapshots),
    }


def _monthly_inquiry_counts(db: Session) -> Dict[str, int]:
    """이달 매칭 요청에서 공단별로 몇 번 추천되었는지 집계 (실제 데이터).
    industrial_parks.json의 고정 monthly_inquiries 목업값을 대체한다."""
    this_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    histories = db.query(MatchingHistory).filter(
        MatchingHistory.created_at >= this_month
    ).all()

    counts: Dict[str, int] = {}
    for h in histories:
        try:
            names = json.loads(h.matched_parks) if h.matched_parks else []
        except Exception:
            names = []
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return counts


@router.get("/dashboard/parks")
def get_parks(db: Session = Depends(get_db), role: str = Depends(require_access)):
    """산업단지 공실 현황 목록 (관공서·관리자 전용)"""
    parks = db.query(IndustrialPark).all()
    inquiry_counts = _monthly_inquiry_counts(db)
    parks.sort(key=lambda p: inquiry_counts.get(p.name, 0), reverse=True)

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
            "monthly_inquiries": inquiry_counts.get(p.name, 0),
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
        })

    return {"parks": result, "total": len(result)}


@router.get("/dashboard/recent-matches")
def get_recent_matches(limit: int = 10, db: Session = Depends(get_db), role: str = Depends(require_access)):
    """최근 매칭 이력 (관공서·관리자 전용)"""
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


@router.patch("/dashboard/matches/{match_id}/status")
def update_match_status(match_id: int, body: MatchStatusUpdate, db: Session = Depends(get_db), role: str = Depends(require_access)):
    """매칭 이력의 실제 진행 상태(현장 방문 예약/입주 확정 등) 기록 (관공서·관리자 전용).
    추후 매칭 정확도 개선(피드백 루프)에 쓸 실제 성사 데이터를 축적하기 위한 엔드포인트."""
    if body.status not in MATCH_STATUS_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"status는 {MATCH_STATUS_OPTIONS} 중 하나여야 합니다.",
        )

    history = db.query(MatchingHistory).filter(MatchingHistory.id == match_id).first()
    if not history:
        raise HTTPException(status_code=404, detail="매칭 이력을 찾을 수 없습니다.")

    history.status = body.status
    db.commit()
    return {"id": history.id, "status": history.status}


@router.get("/dashboard/whoami")
def whoami(role: str = Depends(require_access)):
    """접근 키 검증 + 역할 확인 (프론트엔드 로그인 게이트에서 사용)"""
    return {"role": role}


@router.get("/dashboard/admin/system-status")
def admin_system_status(db: Session = Depends(get_db), role: str = Depends(require_admin)):
    """관리자(SiteMatch 운영진) 전용 — 관공서 화면에는 노출하지 않는 운영 현황."""
    from services.embedding import get_embedding_service
    from services.rag import get_rag_service

    return {
        "ai_matching_ready": get_embedding_service() is not None,
        "rag_chatbot_ready": get_rag_service() is not None,
        "groq_key_set": bool(os.getenv("GROQ_API_KEY")),
        "match_model": "openai/gpt-oss-120b",
        "total_parks": db.query(IndustrialPark).count(),
        "total_matches_all_time": db.query(MatchingHistory).count(),
        "database": "SQLite",
    }


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
