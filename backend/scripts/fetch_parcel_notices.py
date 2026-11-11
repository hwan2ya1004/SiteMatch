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
from services.parcel_notices import JSON_PATH, PARKS_PATH, collect_new, match_park  # noqa: E402


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


if __name__ == "__main__":
    main()
