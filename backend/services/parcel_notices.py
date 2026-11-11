"""산업입지정보센터(industryland.or.kr) 분양공고 수집·저장·조회.

- 사이트 화면이 쓰는 주소를 그대로 쓴다(공식 공개 API 아님):
  목록 POST /il/parcel/list.do {param, pageNo}(10건씩, 최신순), 상세 GET /il/parcel/det.do/{par_seq}
- 서버(Render 무료)는 재배포 때 DB가 비워지므로, 시작할 때 data/parcel_notices.json(커밋된 기준 데이터)으로
  채운 뒤 사이트에서 새 공고만 받아 더한다(sync_new). 매일 04:00에도 sync_new가 돈다.
- 등록 담당자 이름·아이디 등 개인정보는 저장하지 않는다.
"""
import html
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

BASE = "https://www.industryland.or.kr"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(BASE_DIR, "data", "parcel_notices.json")
PARKS_PATH = os.path.join(BASE_DIR, "data", "industrial_parks.json")
DELAY = 0.6
NEW_DAYS = 7  # 공고일이 이 기간 안이면 화면에 "신규" 표시
HEADERS = {"User-Agent": "Mozilla/5.0 (SiteMatch parcel sync)", "Content-Type": "application/json"}
EMPTY_PARAM = {"search_sido": "", "search_gungu": "", "search_danji_nm": "", "search_title": "",
               "search_par_type": "", "search_par_stdm": "", "search_par_eddm": ""}
KEEP = ["par_seq", "title", "danji_nm", "danji_cd", "danji_type_nm", "danji_loc", "addr_si_nm", "addr_gu_nm",
        "par_type_nm", "par_dtm", "par_sdtm", "par_edtm", "divide_area", "par_price", "par_charge", "charge_org",
        "par_dept", "par_reason"]


# ── 단지 연결 ──
SUFFIXES = ("도시첨단산업단지", "국가산업단지", "일반산업단지", "농공단지", "산업단지", "산단", "단지", "농공")


def _norm(s: str) -> str:
    s = re.sub(r"[\(\[<].*?[\)\]>]", "", s or "")
    s = re.sub(r"[\s·.,\-_]", "", s)
    return re.sub(r"제(\d)", r"\1", s)  # "익산제3" = "익산3", "계룡제1" = "계룡1"


def _core(s: str) -> str:
    s = _norm(s)
    for suf in SUFFIXES:
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def _aliases(name: str):
    """정식 이름과 괄호 안 옛 이름([구:입암], <구:성전>)의 핵심 이름들."""
    out = {_core(name)}
    for m in re.findall(r"구\s*[:：]\s*([^\]\)>]+)", name or ""):
        out.add(_core(m))
    return {a for a in out if a}


def _short_city(c: str) -> str:
    c = re.sub(r"\(.*?\)", "", c or "").strip()
    return re.sub(r"(특별자치시|특별자치도|시|군|구)$", "", c.split()[-1]) if c else ""


def match_park(n: Dict, parks: List[Dict]) -> Tuple[Optional[int], Optional[str]]:
    """단지명·시도·시군으로 SiteMatch 단지 id를 찾는다. 하나로 좁혀질 때만 연결한다."""
    # 사이트 표기는 "전북(구)", "익산시(2024-01-18)"처럼 옛 행정구역·날짜가 붙기도 한다
    si = re.sub(r"\(.*?\)", "", n.get("addr_si_nm") or "").strip()
    gu = re.sub(r"\(.*?\)", "", n.get("addr_gu_nm") or "").strip()
    name = (n.get("danji_nm") or "").strip()
    full, site_alias = _norm(name), _aliases(name)
    scity = _short_city(gu)

    def city_ok(p):
        return not gu or not p.get("city") or gu in p["city"] or p["city"] in gu

    def near(a: str, b: str) -> bool:
        """a(사이트)와 b(우리)가 지명 접두어나 '지구'만 다른 같은 단지 이름인지."""
        if a == b:
            return True
        lo, sh = (a, b) if len(a) > len(b) else (b, a)
        return len(sh) >= 2 and (lo == scity + sh or lo == sh + "지구")

    raw = re.sub(r"\s+", "", name)

    def try_in(pool):
        same = [p for p in pool if re.sub(r"\s+", "", p["name"]) == raw]  # 괄호까지 똑같은 이름이 먼저
        if len(same) == 1:
            return same[0]["id"], "name_exact"
        exact = [p for p in pool if _norm(p["name"]) == full]
        if len(exact) > 1:
            exact = [p for p in exact if city_ok(p)] or exact
        if len(exact) == 1:
            return exact[0]["id"], "name_exact"
        # 핵심 이름만 같고(옛 이름 별칭이 아니라) 단지 유형(농공/일반 등)이 다르면 다른 단지다
        # 예: 사이트 "계룡제1농공단지" ≠ 우리 "계룡제1산업단지[구:입암]"(일반산단)
        site_main = _core(name)

        def type_ok(p):
            # 농공단지 ↔ 다른 유형만 구분한다(사이트가 국가산단을 '일반'으로 적는 등 나머지 표기는 들쭉날쭉)
            st = n.get("danji_type_nm") or ""
            if not st or not p.get("type"):
                return True
            return (st == "농공") == p["type"].startswith("농공")

        def alias_match(p):
            pa = _aliases(p["name"])
            if not (site_alias & pa):
                return False
            via_old = (site_alias - {site_main}) & pa or site_alias & (pa - {_core(p["name"])})
            return bool(via_old) or type_ok(p)

        cands = [p for p in pool if alias_match(p)]
        if len(cands) > 1:
            cands = [p for p in cands if city_ok(p)] or cands
        if len(cands) == 1:
            return cands[0]["id"], "name_core"
        part = [p for p in pool if city_ok(p) and type_ok(p)
                and any(near(a, b) for a in site_alias for b in _aliases(p["name"]))]
        if len(part) == 1:
            return part[0]["id"], "name_partial"
        return None, None

    same_si = [p for p in parks if p["region"] == si]
    pid, how = try_in(same_si) if same_si else (None, None)
    if pid is None:  # 시도 표기가 달라 못 찾으면 시군이 맞는 단지 전체에서 다시 찾는다
        pid, how = try_in([p for p in parks if gu and p.get("city") and city_ok(p)])
    return pid, how


# ── 사이트 호출 ──
def fetch_list(page: int) -> Dict:
    r = requests.post(f"{BASE}/il/parcel/list.do", json={"param": EMPTY_PARAM, "pageNo": page}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()["result"]


def fetch_detail(seq: int) -> Dict:
    r = requests.get(f"{BASE}/il/parcel/det.do/{seq}", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("result") or {}


def build_record(d: Dict, parks: List[Dict]) -> Dict:
    rec = {k: d.get(k) for k in KEEP}
    text = html.unescape(d.get("content") or "")
    rec["content_text"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:2000]
    rec["files"] = [f"{f.get('file_name')}{f.get('file_extension') or ''}" for f in (d.get("saveFiles") or [])]
    rec["url"] = f"{BASE}/il/parcel/det/{d.get('par_seq')}"
    rec["park_id"], rec["match"] = match_park(rec, parks)
    return rec


def collect_new(known: set, full: bool = False, log=print) -> Tuple[List[Dict], Optional[int]]:
    """목록을 최신순으로 넘기며 known에 없는 공고를 상세까지 받아 돌려준다.
    full=False면 새 공고가 하나도 없는 쪽에서 멈춘다."""
    parks = json.load(open(PARKS_PATH, encoding="utf-8"))
    seqs, page, total = [], 1, None
    while True:
        res = fetch_list(page)
        total = res.get("cnt", total)
        rows = res.get("dataList") or []
        if not rows:
            break
        fresh = [r["par_seq"] for r in rows if str(r["par_seq"]) not in known]
        seqs += fresh
        if not fresh and not full:
            break
        page += 1
        time.sleep(DELAY)
    out = []
    for i, seq in enumerate(seqs, 1):
        try:
            out.append(build_record(fetch_detail(seq), parks))
        except Exception as e:
            log(f"⚠️ 분양공고 상세 실패 {seq}: {e}")
        if i % 50 == 0:
            log(f"  분양공고 상세 {i}/{len(seqs)}")
        time.sleep(DELAY)
    return out, total


# ── DB ──
def _to_row_kwargs(rec: Dict) -> Dict:
    def num(v):
        try:
            return float(v) if v not in (None, "", 0) else None
        except (TypeError, ValueError):
            return None
    price = num(rec.get("par_price"))
    return dict(
        par_seq=int(rec["par_seq"]), park_id=rec.get("park_id"), title=rec.get("title"),
        danji_nm=rec.get("danji_nm"), danji_cd=str(rec["danji_cd"]) if rec.get("danji_cd") else None,
        region=rec.get("addr_si_nm"), city=rec.get("addr_gu_nm"), par_type=rec.get("par_type_nm"),
        notice_date=rec.get("par_dtm"), start_date=rec.get("par_sdtm"), end_date=rec.get("par_edtm"),
        area=num(rec.get("divide_area")), price=int(price) if price else None,
        org=rec.get("charge_org") or rec.get("par_charge"), phone=rec.get("par_dept"),
        reason=rec.get("par_reason"), url=rec.get("url"),
    )


def upsert(db, recs: List[Dict]) -> int:
    from database import ParcelNotice
    n = 0
    for rec in recs:
        kw = _to_row_kwargs(rec)
        row = db.get(ParcelNotice, kw["par_seq"])
        if row is None:
            db.add(ParcelNotice(**kw))
            n += 1
        else:
            for k, v in kw.items():
                setattr(row, k, v)
    db.commit()
    return n


def seed_from_json(db) -> int:
    """커밋된 기준 데이터로 DB를 채운다(이미 있는 공고는 덮어씀)."""
    if not os.path.exists(JSON_PATH):
        return 0
    store = json.load(open(JSON_PATH, encoding="utf-8"))
    upsert(db, list(store.get("notices", {}).values()))
    from database import ParcelNotice
    return db.query(ParcelNotice).count()


def sync_new(db, log=print) -> int:
    """사이트에서 DB에 없는 새 공고만 받아 저장하고, 새로 추가된 건수를 돌려준다."""
    from database import ParcelNotice
    known = {str(s) for (s,) in db.query(ParcelNotice.par_seq).all()}
    recs, total = collect_new(known, full=False, log=log)
    added = upsert(db, recs) if recs else 0
    log(f"✅ 분양공고 동기화: 새 공고 {added}건 (사이트 전체 {total}건)")
    return added


def serialize(row) -> Dict:
    today = datetime.now().date()
    is_new = False
    if row.notice_date:
        try:
            is_new = (today - datetime.strptime(row.notice_date, "%Y-%m-%d").date()) <= timedelta(days=NEW_DAYS)
        except ValueError:
            pass
    return {
        "par_seq": row.par_seq, "park_id": row.park_id, "title": row.title, "danji_nm": row.danji_nm,
        "region": row.region, "city": row.city, "par_type": row.par_type, "notice_date": row.notice_date,
        "start_date": row.start_date, "end_date": row.end_date, "area": row.area, "price": row.price,
        "org": row.org, "phone": row.phone, "url": row.url, "is_new": is_new,
    }
