"""분양공고 조회 API (산업입지정보센터 분양공고 — services/parcel_notices.py)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import ParcelNotice, get_db
from services.parcel_notices import NEW_DAYS, serialize

router = APIRouter(prefix="/api", tags=["parcels"])
SOURCE = {"name": "산업입지정보센터 분양공고", "url": "https://www.industryland.or.kr/il/parcel/list"}


@router.get("/parcel-notices")
def recent_notices(limit: int = Query(20, ge=1, le=100), region: str = "", db: Session = Depends(get_db)):
    """최근 분양공고(공고일 최신순). region으로 시도를 거를 수 있다."""
    q = db.query(ParcelNotice)
    if region:
        q = q.filter(ParcelNotice.region == region)
    rows = q.order_by(ParcelNotice.notice_date.desc(), ParcelNotice.par_seq.desc()).limit(limit).all()
    items = [serialize(r) for r in rows]
    return {"notices": items, "new_count": sum(1 for i in items if i["is_new"]), "new_days": NEW_DAYS,
            "total": db.query(ParcelNotice).count(), "source": SOURCE}


@router.get("/parks/{park_id}/parcel-notices")
def park_notices(park_id: int, limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)):
    """한 단지에 연결된 분양공고(최신순)."""
    rows = (db.query(ParcelNotice).filter(ParcelNotice.park_id == park_id)
            .order_by(ParcelNotice.notice_date.desc(), ParcelNotice.par_seq.desc()).limit(limit).all())
    return {"notices": [serialize(r) for r in rows], "new_days": NEW_DAYS, "source": SOURCE}
