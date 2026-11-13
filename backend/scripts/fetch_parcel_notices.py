"""산업입지정보센터 분양공고를 받아 data/parcel_notices.json(서버 시작 시 DB를 채우는 기준 데이터)을 갱신한다.
수집·단지 연결 로직은 services/parcel_notices.py와 같다.

사용: python scripts/fetch_parcel_notices.py          (새 공고만 더함)
      python scripts/fetch_parcel_notices.py --all    (전체 다시 받기)
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.parcel_notices import (DOCS_PATH, JSON_PATH, PARKS_PATH, build_doc,  # noqa: E402
                                     collect_new, doc_targets, match_park)


def _save(docs):
    """중간에 죽어도 파일이 반쯤 쓰인 채 남지 않게 임시 파일에 쓴 뒤 바꿔 넣는다."""
    tmp = DOCS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DOCS_PATH)


def main():
    full = "--all" in sys.argv
    store = {"source": "산업입지정보센터 분양공고(https://www.industryland.or.kr/il/parcel/list)", "notices": {}}
    if os.path.exists(JSON_PATH) and not full:
        store = json.load(open(JSON_PATH, encoding="utf-8"))
    recs, total = collect_new(set(store["notices"].keys()), full=full)
    for rec in recs:
        store["notices"][str(rec["par_seq"])] = rec
    # 단지 데이터가 바뀌었을 수 있어 연결을 다시 계산(수동 지정 manual은 유지)
    parks = json.load(open(PARKS_PATH, encoding="utf-8"))
    for rec in store["notices"].values():
        if rec.get("match") != "manual":
            rec["park_id"], rec["match"] = match_park(rec, parks)
    store["updated_at"] = datetime.now().isoformat(timespec="seconds")
    store["total_on_site"] = total
    json.dump(store, open(JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ns = list(store["notices"].values())
    matched = sum(1 for n in ns if n.get("park_id"))
    print(f"새 공고 {len(recs)}건 / 저장 {len(ns)}건 / 단지 연결 {matched}건 / 미연결 {len(ns) - matched}건 → {JSON_PATH}")

    # 첨부 공고문 정리(최근 1년 + 단지별 최신) — 이미 정리한 공고는 건너뛴다(--redo-docs면 다시)
    docs = {"source": store["source"], "docs": {}}
    if os.path.exists(DOCS_PATH) and "--redo-docs" not in sys.argv:
        docs = json.load(open(DOCS_PATH, encoding="utf-8"))
    todo = [s for s in doc_targets(ns) if str(s) not in docs["docs"]]
    print(f"공고문 정리 대상 {len(todo)}건")
    for i, seq in enumerate(todo, 1):
        try:
            docs["docs"][str(seq)] = build_doc(seq)
        except Exception as e:
            print(f"  실패 {seq}: {e}")
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}")
            _save(docs)
    docs["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(docs)
    from collections import Counter
    print("공고문 정리 결과:", dict(Counter(d["status"] for d in docs["docs"].values())))


if __name__ == "__main__":
    main()
