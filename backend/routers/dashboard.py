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

from database import get_db, IndustrialPark, MatchingHistory, VacancySnapshot, ParkVacancySnapshot, ParkInquiry
from data.national_stats import get_national_park_stats

router = APIRouter(prefix="/api", tags=["dashboard"])


class InquiryCreate(BaseModel):
    park_name: str
    company_name: str = ""
    contact: str = ""
    industry: str = ""
    message: str


class InquiryReply(BaseModel):
    reply: str



# 아래 두 함수는 로그인 화면을 별도로 만들 때까지 라우트에 적용하지 않고 대기시켜둔 상태.
# (지금은 모든 /dashboard/* 및 concierge 처리 큐 엔드포인트가 키 없이 열려 있음 — 사용자 요청으로 임시 원복.
# 로그인 기능 구현 시 콘시어지 처리 큐(개인정보 포함)부터 최우선으로 보호할 것)
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


@router.get("/dashboard/stats")
def get_stats(db: Session = Depends(get_db)):
    """대시보드 핵심 통계 (인증 없음 — 로그인 화면 준비 전까지 임시로 공개)"""
    parks = db.query(IndustrialPark).all()

    total_available = sum(p.available_area or 0 for p in parks)
    avg_vacancy = (sum(p.vacancy_rate or 0 for p in parks) / len(parks)) if parks else 0

    # 이달 매칭 건수
    this_month = datetime.now().replace(day=1, hour=0, minute=0, second=0)
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
        "total_parks": len(parks),  # 서비스에 등록된 샘플 단지 수 (전국 총계 아님)
        "national": get_national_park_stats(),  # 전국 산업단지 총계·유형별 분포 (TAM 참고용)
    }


def _monthly_inquiry_counts(db: Session) -> Dict[str, int]:
    """이달 매칭 요청에서 공단별로 몇 번 추천되었는지 집계 (실제 데이터).
    industrial_parks.json의 고정 monthly_inquiries 목업값을 대체한다."""
    this_month = datetime.now().replace(day=1, hour=0, minute=0, second=0)
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


# 특정 산단 개별로 실제 관리기관을 웹서치로 확인해둔 것 (subsidy_docs.txt
# "경기도 내 다른 산업단지 관리기관 안내" 섹션과 동일 출처). (park명, 소재 시·군) 정확히
# 일치할 때만 적용 — 같은 시에 있는 다른 산단까지 같은 기관으로 단정하면 안 되므로
# 도시 단위가 아니라 반드시 개별 산단명으로 매칭한다.
_MANAGEMENT_ORG_OVERRIDES = {
    ("마도", "화성시"): "화성도시공사 산업단지관리사업소",
    ("파주LCD", "파주시"): "파주시 평화경제과",
    ("관리", "이천시"): "이천시 (민원콜센터 031-644-2000 경유)",
}


# 산업집적법 제30조상 "관리권자" 직함. region은 국가법령정보센터 통계 표기(약칭)라
# 특별시·광역시·특별자치시는 시장, 도·특별자치도는 도지사가 맞게 매핑해둔다.
_REGION_HEAD_TITLE = {
    "서울": "서울특별시장", "부산": "부산광역시장", "대구": "대구광역시장",
    "인천": "인천광역시장", "광주": "광주광역시장", "대전": "대전광역시장",
    "울산": "울산광역시장", "세종": "세종특별자치시장",
    "경기": "경기도지사", "강원": "강원특별자치도지사",
    "충북": "충청북도지사", "충남": "충청남도지사",
    "전북": "전북특별자치도지사", "전남": "전라남도지사",
    "경북": "경상북도지사", "경남": "경상남도지사", "제주": "제주특별자치도지사",
}


def _management_org(park: "IndustrialPark") -> str:
    """산업단지 관리기관을 산업집적법 제30조 기준으로 안내한다.
    - 개별 산단 단위로 실제 확인된 경우(_MANAGEMENT_ORG_OVERRIDES)는 그 기관명을 그대로 사용
    - 국가산단은 관리업무가 실질적으로 한국산업단지공단(KICOX)에 위탁되어 있어 전국 공통으로 확신 가능
    - 일반산단·도시첨단산단의 법상 관리권자는 "시·도지사"(광역 단위)이지 시청(기초지자체)이 아님
    - 농공단지의 법상 관리권자는 "시장·군수·구청장"(기초지자체 단위)
    - 어느 쪽이든 실무는 한국산업단지공단·산업단지관리공단·입주기업체협의회 등에 위탁될 수 있고
      실제 위탁 여부·기관명은 산단마다 달라 법률 문서만으로는 알 수 없다 — 개별 확인 없이
      특정 위탁기관명을 단정하지 않는다(사실에 근거해야 한다는 원칙)"""
    override = _MANAGEMENT_ORG_OVERRIDES.get((park.name, park.city))
    if override:
        return override
    if park.type == "국가산단":
        return "한국산업단지공단(KICOX)"
    if park.type == "농공산단":
        city = park.city or "관할 시·군·구"
        return f"{city} 시장·군수 (관리권자, 산업집적법 제30조) — 위탁관리기관 있으면 산단별 확인 필요"
    head = _REGION_HEAD_TITLE.get(park.region or "")
    if head:
        return f"{head} (관리권자, 산업집적법 제30조) — 실무 창구는 통상 {park.city or '관할 시·군·구'} 또는 위탁관리기관(공단 등), 산단별 확인 필요"
    city = park.city or park.region or "관할 지자체"
    return f"{city} (관할 지자체 — 위탁관리기관은 산단별로 다를 수 있어 개별 확인 필요)"


@router.get("/dashboard/parks")
def get_parks(db: Session = Depends(get_db)):
    """산업단지 공실 현황 목록 (인증 없음 — 임시 공개)"""
    parks = db.query(IndustrialPark).all()
    inquiry_counts = _monthly_inquiry_counts(db)
    parks.sort(key=lambda p: p.name)  # 이름순(가나다) — "내 산업단지" 셀렉트와 동일한 정렬 기준

    result = []
    for p in parks:
        # 공실률(등록 대비 미가동 비율)만 보고 "여유"를 매기면, 이미 완공되어
        # 분양·입주가 다 끝난(가용면적 0) 단지도 "여유"로 표시되는 모순이 생긴다
        # (실제로 발생 — 가용면적 0㎡인데 "여유"로 떠서 혼란을 준 사례). 가용면적이
        # 없으면 공실률 수치와 무관하게 "포화"를 최우선으로 표시한다.
        if p.dev_status == "완료" and not (p.available_area and p.available_area > 0):
            status = "포화"
            status_class = "high"
            bar_color = "#E24B4A"
        elif p.vacancy_rate and p.vacancy_rate >= 25:
            status = "주의"
            status_class = "high"
            bar_color = "#E24B4A"
        elif p.vacancy_rate and p.vacancy_rate >= 15:
            status = "보통"
            status_class = "mid"
            bar_color = "#EF9F27"
        else:
            status = "여유"
            status_class = "low"
            bar_color = "#639922"

        result.append({
            "id": p.id,
            "name": p.name,
            "city": p.city,
            "region": p.region,
            "type": p.type or "",
            "dev_status": p.dev_status or "완료",
            "address": p.address or "",
            "lat": p.lat,
            "lng": p.lng,
            # 가동률·가용면적·조성상태를 매번 조합 해석하지 않아도 되도록 "입주 가능/불가"로
            # 정리 — match.py의 move_in_status()와 동일 기준(조성상태 미완료 또는 가용면적 0이면 불가)
            "move_in_status": "입주 가능" if (p.dev_status or "완료") == "완료" and p.available_area and p.available_area > 0 else "입주 불가",
            "management_org": _management_org(p),
            "vacancy_rate": p.vacancy_rate or 0,
            "sale_rate": p.sale_rate,  # 분양률(%) — None이면 정보없음(0으로 대신하지 않음)
            "available_area": f"{p.available_area:,.0f}㎡" if p.available_area else "0㎡",
            "available_area_raw": p.available_area or 0,
            "rent_per_sqm": f"{p.rent_per_sqm:,}원/㎡" if p.rent_per_sqm else "정보없음",
            "industries": json.loads(p.industries) if p.industries else [],
            "status": status,
            "status_class": status_class,
            "bar_color": bar_color,
            "monthly_inquiries": inquiry_counts.get(p.name, 0),
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
        })

    return {"parks": result, "total": len(result)}


@router.post("/inquiries")
def create_inquiry(body: InquiryCreate, db: Session = Depends(get_db)):
    """산업단지 입주 문의 등록 (소비자용, 인증 없음).
    관리기관과 직접 연결되는 창구가 없어 챗봇이 안내한 외부 연락처에서 막히는
    문제를 보완하기 위해, 문의를 남기면 해당 산단의 관공서 대시보드에 접수된다."""
    if not body.park_name.strip():
        raise HTTPException(status_code=400, detail="산업단지를 선택해주세요.")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="문의 내용을 입력해주세요.")

    inquiry = ParkInquiry(
        park_name=body.park_name.strip(),
        company_name=body.company_name.strip() or "익명",
        contact=body.contact.strip(),
        industry=body.industry.strip(),
        message=body.message.strip(),
        status="접수",
    )
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)
    return {"id": inquiry.id, "status": inquiry.status}


@router.get("/dashboard/inquiries")
def get_inquiries(park: str = "", limit: int = 50, db: Session = Depends(get_db)):
    """산업단지 입주 문의 목록 (관공서 대시보드용, 인증 없음 — 임시 공개).
    park가 주어지지 않으면 빈 목록을 반환한다 — 관리기관마다 담당 산단이 달라
    "전체 보기" 상태에서 다른 산단으로 온 문의(연락처 포함)까지 노출되지 않도록
    프런트에서 특정 산단을 선택했을 때만 호출하는 것을 전제로 한다."""
    if not park:
        return {"inquiries": [], "total": 0}

    rows = (
        db.query(ParkInquiry)
        .filter(ParkInquiry.park_name == park)
        .order_by(desc(ParkInquiry.created_at))
        .limit(limit)
        .all()
    )
    return {
        "inquiries": [
            {
                "id": r.id,
                "park_name": r.park_name,
                "company_name": r.company_name,
                "contact": r.contact,
                "industry": r.industry,
                "message": r.message,
                "reply": r.reply,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "replied_at": r.replied_at.isoformat() if r.replied_at else "",
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.patch("/dashboard/inquiries/{inquiry_id}")
def reply_inquiry(inquiry_id: int, body: InquiryReply, db: Session = Depends(get_db)):
    """입주 문의에 담당자 답변 등록 (관공서 대시보드용, 인증 없음 — 임시 공개)."""
    if not body.reply.strip():
        raise HTTPException(status_code=400, detail="답변 내용을 입력해주세요.")

    inquiry = db.query(ParkInquiry).filter(ParkInquiry.id == inquiry_id).first()
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")

    inquiry.reply = body.reply.strip()
    inquiry.status = "답변완료"
    inquiry.replied_at = datetime.now()
    db.commit()
    return {"id": inquiry.id, "status": inquiry.status}


@router.get("/dashboard/whoami")
def whoami(role: str = Depends(require_access)):
    """접근 키 검증 + 역할 확인 (프론트엔드 로그인 게이트에서 사용)"""
    return {"role": role}


@router.get("/dashboard/vacancy-trend")
def vacancy_trend(days: int = 30, db: Session = Depends(get_db)):
    """전체 평균 공실 현황 추이 (일별 스냅샷, services/public_data.py의
    daily_etl_job이 매일 새벽 2시에 기록). API 장애로 그날 갱신이 안 됐어도
    직전 값이 그대로 스냅샷에 남아 그래프에 공백이 생기지 않는다."""
    from services.public_data import PublicDataService
    svc = PublicDataService()
    return {"trend": svc.get_vacancy_trend(db, days=days)}


@router.get("/dashboard/admin/system-status")
def admin_system_status(db: Session = Depends(get_db)):
    """관리자 화면에서만 노출하는 운영 현황 (인증 없음 — 임시 공개, URL을 알면 접근은 가능함)."""
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


@router.get("/parks/{park_id}/documents")
def get_park_documents(park_id: int, db: Session = Depends(get_db)):
    """해당 단지의 "전국산업단지 공문" 폴더에 있는 문서/이미지 목록.
    로컬 실행 시에만 폴더가 존재하므로(배포 환경엔 없음) 없으면 그냥 빈 목록을 반환한다."""
    from services.park_docs import list_park_documents

    park = db.query(IndustrialPark).filter(IndustrialPark.id == park_id).first()
    if not park:
        raise HTTPException(status_code=404, detail="해당 산업단지를 찾을 수 없습니다.")

    docs = list_park_documents(park.region or "", park.city or "", park.name or "")
    for d in docs:
        d["url"] = f"/api/parks/{park_id}/documents/{d['filename']}"
    return {"park_id": park_id, "park_name": park.name, "documents": docs}


@router.get("/parks/{park_id}/documents/{filename}")
def download_park_document(park_id: int, filename: str, db: Session = Depends(get_db)):
    """개별 문서 다운로드. list_park_documents가 반환한 파일명과 정확히 일치할 때만
    서빙한다 — 임의의 filename으로 폴더 밖 파일에 접근(경로 조작)하는 것을 막기 위함."""
    from fastapi.responses import FileResponse
    from services.park_docs import resolve_document_path

    park = db.query(IndustrialPark).filter(IndustrialPark.id == park_id).first()
    if not park:
        raise HTTPException(status_code=404, detail="해당 산업단지를 찾을 수 없습니다.")

    path = resolve_document_path(park.region or "", park.city or "", park.name or "", filename)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다.")

    return FileResponse(str(path), filename=filename)


@router.get("/parks/{park_id}/boundary")
def get_park_boundary_endpoint(park_id: int, db: Session = Depends(get_db)):
    """해당 단지의 실제 경계 폴리곤(국토교통부 산업단지 경계도면 고시 기준).
    매칭되는 경계 데이터가 없으면 rings: null을 반환한다(추측 좌표를 만들지 않음)."""
    from services.park_boundaries import get_park_boundary

    park = db.query(IndustrialPark).filter(IndustrialPark.id == park_id).first()
    if not park:
        raise HTTPException(status_code=404, detail="해당 산업단지를 찾을 수 없습니다.")

    rings = get_park_boundary(park_id)
    return {"park_id": park_id, "park_name": park.name, "rings": rings}
