"""
확인대행 / 원스톱 동행 / 민원대행 요청 라우터.
사람이 직접 처리해야 하는 프리미엄 서비스 — SiteMatch 운영진이 큐를 보고
직접 관리기관에 확인하거나 입주 과정을 챙긴 뒤, 결과를 이 시스템에 기록한다.

⚠️ 법적 주의 (민원대행 · 원스톱동행 공통): 행정기관(팩토리온)에 제출하는 서류를
유상으로 대신 작성·제출하는 행위는 행정사법상 행정사 자격이 있어야 하는 규제 영역이다.
"원스톱동행"도 정의상 "서류 준비"·"계약까지 동행"을 포함하므로 실질적으로 같은 위험이
있다고 보고 동일하게 묶는다 — 이름이 다르다고 규제를 피할 수 없다.
무자격 대행은 행정사법 위반(형사처벌 대상)이므로, 행정사 자격 보유자를 채용해
MINWON_AGENT_LICENSED=true를 설정하기 전까지는 두 유형 모두 신청 자체를 막는다.
"확인대행"(단순 전화로 정보만 확인·전달)만 서류 작성·제출이 아니므로 이 제약과 무관.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, ConciergeRequest

router = APIRouter(prefix="/api", tags=["concierge"])

MINWON_AGENT_LICENSED = os.getenv("MINWON_AGENT_LICENSED", "false").lower() == "true"

# 민원대행·원스톱동행은 정규직 고용이 아니라 "프리랜서 행정사와의 건별 용역계약" 구조로
# 확정했다 — 행정사는 자기 명의 사무소·사업자등록을 유지하고, SiteMatch는 고객에게 받은
# 금액 중 일부만 플랫폼 수수료로 남기고 나머지를 행정사에게 지급한다. 정확한 배분율은
# 아직 실제 행정사와 협의 전 가정치(30%)이며, 확정되면 이 값을 바꾸면 된다.
# ("확인대행"은 자격 불필요한 자체 직원 업무라 이 배분 구조와 무관 — 정규직 급여로 지급)
PLATFORM_MARGIN_RATIO = 0.30  # SiteMatch 몫. 나머지 70%는 프리랜서 행정사 몫

# 원스톱동행 시간당 단가는 업무시간대냐 아니냐로 갈린다 — 관공서·관리기관 자체가
# 평일 오전 10시~오후 6시에만 운영되므로 실제 노동시간 대부분은 이 구간에 해당하고,
# 이동 등으로 이 구간을 벗어난 시간만 낮은 단가가 적용된다.
BUSINESS_HOUR_START = 10  # 오전 10시
BUSINESS_HOUR_END = 18    # 오후 6시
BUSINESS_RATE_WON = 20_000     # 업무시간대(10~18시) 시간당 단가
AFTER_HOURS_RATE_WON = 10_000  # 그 외 시간대 시간당 단가

# 의뢰 접수(created_at)~완료(completed_at) 시각 차이는 쓰지 않는다 — 산업단지 입주계약
# 처리기간은 공식 기준 6~10영업일(산업집적법 시행규칙 제34조)이라 그대로 시간 환산하면
# 수백 시간이 되어버려 시간당 단가와 맞지 않음. 대신 행정사가 실제로 붙어 일한 시작~종료
# 시각(work_started_at~work_ended_at)만 관리자가 온라인(관리자 대시보드)에서 직접 입력하고,
# 그 구간이 업무시간대와 겹치는 만큼만 계산한다.
FEE_NOTE_BY_TYPE = {
    "확인대행": "건당 3만원",
    "원스톱동행": (
        f"업무시간(10~18시) 시간당 {BUSINESS_RATE_WON:,}원 · 그 외 시간당 {AFTER_HOURS_RATE_WON:,}원 "
        f"× 실투입시간(관리자가 시작~종료 시각을 온라인으로 직접 기록) — "
        f"SiteMatch {int(PLATFORM_MARGIN_RATIO*100)}% / 행정사 {int((1-PLATFORM_MARGIN_RATIO)*100)}% 배분(가정치), "
        "왕복 교통비·식비 등 실비는 별도 정산"
    ),
    # 팩토리온(factoryon.go.kr)에 실제로 접수하는 행위를 담당자가 대신 수행한다.
    # 팩토리온과 시스템 연동은 없음 — 담당자가 사람 손으로 직접 접수(자동화 아님).
    "민원대행": f"건당 10만원 — SiteMatch {int(PLATFORM_MARGIN_RATIO*100)}% / 행정사 {int((1-PLATFORM_MARGIN_RATIO)*100)}% 배분(가정치)",
}


def split_fee(total_fee_krw: float) -> dict:
    """총 수수료를 플랫폼 몫/행정사 몫으로 나눈다 (가정 배분율, 실제 협의 후 확정 필요)."""
    platform_share = round(total_fee_krw * PLATFORM_MARGIN_RATIO)
    return {
        "total": total_fee_krw,
        "platform_share": platform_share,
        "agent_share": total_fee_krw - platform_share,
    }


def split_business_hours(start: datetime, end: datetime) -> tuple:
    """[start, end) 구간을 날짜별로 나눠 업무시간(10~18시) 안/밖 시간을 각각 합산한다."""
    if end <= start:
        return 0.0, 0.0
    business_seconds = 0.0
    cur = start
    while cur < end:
        day_biz_start = cur.replace(hour=BUSINESS_HOUR_START, minute=0, second=0, microsecond=0)
        day_biz_end = cur.replace(hour=BUSINESS_HOUR_END, minute=0, second=0, microsecond=0)
        next_midnight = (cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        seg_end = min(end, next_midnight)
        overlap_start = max(cur, day_biz_start)
        overlap_end = min(seg_end, day_biz_end)
        if overlap_end > overlap_start:
            business_seconds += (overlap_end - overlap_start).total_seconds()
        cur = seg_end
    total_hours = (end - start).total_seconds() / 3600
    business_hours = business_seconds / 3600
    after_hours = max(total_hours - business_hours, 0)
    return round(business_hours, 2), round(after_hours, 2)


def calc_onestop_fee(work_started_at: datetime, work_ended_at: datetime) -> dict:
    """원스톱동행 수수료 = 관리자가 입력한 실제 작업 시작~종료 시각을 업무시간대(10~18시,
    시간당 2만원)와 그 외(시간당 1만원)로 나눠 계산. 왕복 교통비·식비 등 실비는 별도."""
    business_hours, after_hours = split_business_hours(work_started_at, work_ended_at)
    total_fee = round(business_hours * BUSINESS_RATE_WON + after_hours * AFTER_HOURS_RATE_WON)
    split = split_fee(total_fee)
    split["hours_business"] = business_hours
    split["hours_after_hours"] = after_hours
    return split

# 팩토리온 민원 유형 (산업단지 외/개별입지, 산업단지 내/계획입지) — subsidy_docs.txt의
# [공장설립·입주 민원 절차 안내] 항목과 동일한 목록. request_type="민원대행"일 때 이 중 하나를 고른다.
MINWON_TYPES = [
    # 산업단지 외(개별입지)
    "신설", "신설변경", "증설", "증설변경", "이전", "이전변경",
    "업종변경", "업종변경변경", "제조시설설치", "제조시설설치변경",
    "공장설립계획", "공장설립계획변경", "변경신고", "신규등록",
    "완료신고", "부분등록", "등록변경", "건축물등록",
    "민원취소", "취하원", "취소원",
    # 산업단지 내(계획입지) — SiteMatch 추천 대상은 대부분 이쪽
    "입주계약", "입주계약변경", "사업개시신고", "처분신청", "처분신고",
    "임대신고", "입주계약해지",
]


class ConciergeCreate(BaseModel):
    request_type: str = "확인대행"  # 확인대행 / 원스톱동행 / 민원대행
    minwon_type: Optional[str] = None  # request_type="민원대행"일 때 필수
    park_name: str
    company_name: str = ""
    contact: str
    message: str = ""


class ConciergeUpdate(BaseModel):
    status: str  # 접수 / 확인중 / 완료
    result: Optional[str] = None
    work_started_at: Optional[datetime] = None  # request_type="원스톱동행"이고 완료 처리할 때 사용
    work_ended_at: Optional[datetime] = None


@router.get("/concierge/minwon-types")
def get_minwon_types():
    """민원대행 신청 폼의 민원 유형 드롭다운용 목록 (팩토리온 실제 민원 체계 기준)."""
    return {"types": MINWON_TYPES}


@router.post("/concierge")
def create_concierge_request(body: ConciergeCreate, db: Session = Depends(get_db)):
    """고객이 확인대행/원스톱 동행을 신청한다 (인증 없음, 공개)."""
    if not body.park_name.strip():
        raise HTTPException(status_code=400, detail="산업단지를 선택해주세요.")
    if not body.contact.strip():
        raise HTTPException(status_code=400, detail="연락처를 입력해주세요.")
    if body.request_type not in FEE_NOTE_BY_TYPE:
        raise HTTPException(status_code=400, detail="요청 유형이 올바르지 않습니다.")
    if body.request_type in ("민원대행", "원스톱동행") and not MINWON_AGENT_LICENSED:
        raise HTTPException(
            status_code=403,
            detail=f"{body.request_type}은(는) 행정사 자격 보유자가 채용된 뒤에만 제공 가능합니다 "
                    "(서류 준비·제출을 포함해 행정사법상 무자격 대행 금지). 현재는 신청할 수 없습니다.",
        )
    if body.request_type == "민원대행" and body.minwon_type not in MINWON_TYPES:
        raise HTTPException(status_code=400, detail="민원 유형을 선택해주세요.")

    req = ConciergeRequest(
        request_type=body.request_type,
        minwon_type=body.minwon_type if body.request_type == "민원대행" else None,
        park_name=body.park_name.strip(),
        company_name=body.company_name.strip() or "익명",
        contact=body.contact.strip(),
        message=body.message.strip(),
        status="접수",
        fee_note=FEE_NOTE_BY_TYPE[body.request_type],
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    resp = {"id": req.id, "status": req.status, "fee_note": req.fee_note}
    if body.request_type == "민원대행":
        resp["split"] = split_fee(100_000)  # 정액이라 신청 시점에 바로 계산 가능
    # "원스톱동행"은 실제 투입 시간을 알아야 총액이 나오므로, 완료 처리 시 관리자가
    # work_started_at/work_ended_at을 입력하면 그때 자동 계산된다(calc_onestop_fee 참조).
    return resp


@router.get("/dashboard/concierge")
def list_concierge_requests(status: str = "", db: Session = Depends(get_db)):
    """운영진용 처리 큐. 회사명·연락처 등 개인정보를 포함하나, 로그인 기능이 아직 없어
    현재는 인증 없이 열려 있음(임시) — 로그인 기능 구현 시 반드시 관리자 인증으로 보호할 것."""
    query = db.query(ConciergeRequest)
    if status:
        query = query.filter(ConciergeRequest.status == status)
    rows = query.order_by(ConciergeRequest.created_at.desc()).all()

    return {
        "requests": [
            {
                "id": r.id,
                "request_type": r.request_type,
                "minwon_type": r.minwon_type,
                "park_name": r.park_name,
                "company_name": r.company_name,
                "contact": r.contact,
                "message": r.message,
                "status": r.status,
                "result": r.result,
                "fee_note": r.fee_note,
                "work_started_at": r.work_started_at.isoformat() if r.work_started_at else "",
                "work_ended_at": r.work_ended_at.isoformat() if r.work_ended_at else "",
                "hours_business": r.hours_business,
                "hours_after_hours": r.hours_after_hours,
                "fee_krw": r.fee_krw,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "completed_at": r.completed_at.isoformat() if r.completed_at else "",
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.patch("/dashboard/concierge/{request_id}")
def update_concierge_request(request_id: int, body: ConciergeUpdate, db: Session = Depends(get_db)):
    """운영진이 처리 상태·결과를 기록한다. 로그인 기능 구현 전까지 인증 없이 열려 있음(임시)."""
    if body.status not in ("접수", "확인중", "완료"):
        raise HTTPException(status_code=400, detail="상태 값이 올바르지 않습니다.")

    req = db.query(ConciergeRequest).filter(ConciergeRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")

    req.status = body.status
    if body.result is not None:
        req.result = body.result.strip()
    if body.work_started_at is not None:
        req.work_started_at = body.work_started_at
    if body.work_ended_at is not None:
        req.work_ended_at = body.work_ended_at
    if body.status == "완료" and not req.completed_at:
        req.completed_at = datetime.now()

    resp = {"id": req.id, "status": req.status}
    if (
        req.request_type == "원스톱동행"
        and req.status == "완료"
        and req.work_started_at
        and req.work_ended_at
    ):
        split = calc_onestop_fee(req.work_started_at, req.work_ended_at)
        req.hours_business = split["hours_business"]
        req.hours_after_hours = split["hours_after_hours"]
        req.fee_krw = split["total"]
        resp["split"] = split

    db.commit()
    return resp
