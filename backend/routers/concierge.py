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
from datetime import datetime
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

FEE_NOTE_BY_TYPE = {
    "확인대행": "건당 3만원",
    # 정액이 아니라 연간 임대료의 1% — 주택(아파트) 임대차 중개보수는 소비자 보호용
    # 법정 상한요율(0.3~0.9%)이 있지만, 비주택(공장·산업용지) 부동산은 이 캡이 적용되지
    # 않아 실무상 더 높게 협의되는 경우가 많다. 실제 계약 완료 시 담당자가 임대료를
    # 확인해 수수료를 계산·기재한다(자동 계산 아님, result 필드에 기록).
    "원스톱동행": f"연간 임대료의 1%(입주 성사 시 1회, 중형 거래 기준 약 40만원) — SiteMatch {int(PLATFORM_MARGIN_RATIO*100)}% / 행정사 {int((1-PLATFORM_MARGIN_RATIO)*100)}% 배분(가정치)",
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
    # "원스톱동행"은 실제 임대료를 알아야 총액이 나오므로, 완료 처리 시 운영진이 계산해 result에 기록한다.
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
    if body.status == "완료" and not req.completed_at:
        req.completed_at = datetime.now()
    db.commit()
    return {"id": req.id, "status": req.status}
