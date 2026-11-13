"""분양공고 조회 API (산업입지정보센터 분양공고 — services/parcel_notices.py)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import ParcelNotice, get_db
from services.parcel_notices import NEW_DAYS, serialize

router = APIRouter(prefix="/api", tags=["parcels"])
SOURCE = {"name": "산업입지정보센터 분양공고", "url": "https://www.industryland.or.kr/il/parcel/list"}
RECENT_DAYS = 90  # 지도에서 핀을 강조하는 "최근 분양공고" 기준


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


@router.get("/parcel-notices/by-park")
def notices_by_park(db: Session = Depends(get_db)):
    """지도용 요약 — 단지별 분양공고 건수와 가장 최근 공고(공고일·구분·신규/최근 여부)."""
    from collections import defaultdict
    from datetime import datetime, timedelta

    rows = db.query(ParcelNotice).filter(ParcelNotice.park_id.isnot(None)).all()
    groups = defaultdict(list)
    for r in rows:
        groups[r.park_id].append(r)
    today = datetime.now().date()
    out = {}
    for pid, rs in groups.items():
        rs.sort(key=lambda r: (r.notice_date or "", r.par_seq), reverse=True)
        top = serialize(rs[0])
        recent = False
        if top["notice_date"]:
            try:
                recent = (today - datetime.strptime(top["notice_date"], "%Y-%m-%d").date()) <= timedelta(days=RECENT_DAYS)
            except ValueError:
                pass
        # 지금 접수 중인 공고(공고일이 오래돼도 신청기간이 남은 것) — 지도 라벨에 "접수중"으로 표시
        t = today.strftime("%Y-%m-%d")
        opened = [r for r in rs if r.start_date and r.end_date and r.start_date <= t <= r.end_date]
        # 지도 라벨(분양가·면적)은 접수 중인 공고가 있으면 그것, 없으면 가장 최근 공고 기준
        lab = opened[0] if opened else rs[0]
        out[pid] = {"count": len(rs), "latest_date": top["notice_date"], "latest_type": top["par_type"],
                    "latest_title": top["title"], "is_new": top["is_new"], "recent": recent,
                    "open": bool(opened), "label_price": lab.price, "label_area": lab.area,
                    "label_end": lab.end_date}
    return {"parks": out, "new_days": NEW_DAYS, "recent_days": RECENT_DAYS, "source": SOURCE}


@router.get("/parcel-notices/{par_seq}")
def notice_detail(par_seq: int, db: Session = Depends(get_db)):
    """공고 하나 — 핵심 정보와 첨부 공고문을 항목별로 나눈 내용(원문 글자 그대로)."""
    import json
    from fastapi import HTTPException

    row = db.get(ParcelNotice, par_seq)
    if row is None:
        raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다")
    doc = json.loads(row.doc_json) if row.doc_json else None
    return {"notice": serialize(row), "doc": doc, "source": SOURCE}


@router.get("/parks/{park_id}/parcel-notices")
def park_notices(park_id: int, limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)):
    """한 단지에 연결된 분양공고(최신순)."""
    rows = (db.query(ParcelNotice).filter(ParcelNotice.park_id == park_id)
            .order_by(ParcelNotice.notice_date.desc(), ParcelNotice.par_seq.desc()).limit(limit).all())
    return {"notices": [serialize(r) for r in rows], "new_days": NEW_DAYS, "source": SOURCE}
