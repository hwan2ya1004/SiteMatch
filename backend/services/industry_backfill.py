"""
등록공장 생산정보 API(getFctryListInIrsttService_v2)로 산업단지별 '업종' 필드를
실제 데이터로 채우는 백필 작업.

data.go.kr 승인 건이 일일 트래픽 1,000회로 제한돼 있어, 하루 최대 1,000개 단지까지만
처리하고 진행 상황을 파일에 저장해 다음날 이어서 실행한다 (국가산단부터 우선 처리).
"""
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARKS_PATH = os.path.join(BASE_DIR, "data", "industrial_parks.json")
PROGRESS_PATH = os.path.join(BASE_DIR, "data", "industry_backfill_progress.json")

FCTRY_URL = "https://apis.data.go.kr/B550624/fctryRegistInfo/getFctryListInIrsttService_v2"

DAILY_CALL_BUDGET = 1000

TYPE_PRIORITY = {"국가산단": 0, "도시첨단산단": 1, "일반산단": 2, "농공산단": 3}


def _load_progress() -> Dict:
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"done_ids": [], "failed_ids": [], "last_run_date": None, "calls_used_today": 0}


def _save_progress(progress: Dict) -> None:
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def _clean_industry_name(raw: str) -> Optional[str]:
    """'그 외 기타 전자부품 제조업 외 5 종' 같은 표기에서 실제 업종명만 남긴다."""
    if not raw:
        return None
    name = re.sub(r"\s*외\s*\d+\s*종\s*$", "", raw).strip()
    return name or None


def fetch_top_industries(irstt_nm: str, api_key: str, sample_size: int = 30) -> List[str]:
    """해당 산업단지의 등록공장 표본을 조회해 가장 흔한 업종 상위 5개를 반환한다."""
    resp = requests.get(FCTRY_URL, params={
        "serviceKey": api_key,
        "pageNo": 1,
        "numOfRows": sample_size,
        "irsttNm": irstt_nm,
        "type": "json",
    }, timeout=15)
    resp.raise_for_status()

    text = resp.text
    items = []
    if text.strip().startswith("<"):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text)
        for item in root.findall(".//item"):
            items.append({child.tag: child.text for child in item})
    else:
        data = resp.json()
        body = data.get("response", {}).get("body", {})
        raw_items = body.get("items", {})
        if isinstance(raw_items, dict):
            raw_items = raw_items.get("item", [])
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        items = raw_items or []

    counts: Dict[str, int] = {}
    for it in items:
        name = _clean_industry_name(it.get("indutyNm", ""))
        if name:
            counts[name] = counts.get(name, 0) + 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in ranked[:5]]


def run_backfill(api_key: str, budget: int = DAILY_CALL_BUDGET) -> Dict:
    """오늘 호출 예산 안에서 아직 처리 안 된 단지의 업종 정보를 채운다."""
    with open(PARKS_PATH, "r", encoding="utf-8") as f:
        parks = json.load(f)

    MAX_RETRIES = 3  # 이 횟수만큼 실패하면 영구 제외 (타임아웃 등 일시 오류는 그 전에 재시도됨)

    progress = _load_progress()
    today = datetime.now().strftime("%Y-%m-%d")  # 서버 로컬(한국) 시간 기준 — data.go.kr 일일 한도도 KST 기준으로 리셋됨
    if progress.get("last_run_date") != today:
        progress["calls_used_today"] = 0
        progress["last_run_date"] = today

    done_ids = set(progress.get("done_ids", []))
    retry_counts: Dict[str, int] = progress.get("retry_counts", {})  # id(str) -> 실패 횟수
    giveup_ids = {int(k) for k, v in retry_counts.items() if v >= MAX_RETRIES}

    # 실패했지만 아직 재시도 여지가 있는 항목은 pending에 다시 포함시킨다 (타임아웃 등 일시 오류 대응)
    pending = [p for p in parks if p["id"] not in done_ids and p["id"] not in giveup_ids]
    pending.sort(key=lambda p: TYPE_PRIORITY.get(p.get("type"), 9))

    remaining_budget = budget - progress.get("calls_used_today", 0)
    processed, updated, errored = 0, 0, 0

    def _save_now():
        progress["done_ids"] = sorted(done_ids)
        progress["retry_counts"] = retry_counts
        _save_progress(progress)
        with open(PARKS_PATH, "w", encoding="utf-8") as f:
            json.dump(parks, f, ensure_ascii=False, indent=2)

    for park in pending:
        if remaining_budget <= 0:
            break
        try:
            industries = fetch_top_industries(park["name"], api_key)
            if industries:
                park["industries"] = industries
                updated += 1
            done_ids.add(park["id"])
            retry_counts.pop(str(park["id"]), None)
        except Exception as e:
            print(f"⚠️ 업종 조회 실패 ({park['name']}): {e}")
            retry_counts[str(park["id"])] = retry_counts.get(str(park["id"]), 0) + 1
            errored += 1
        processed += 1
        remaining_budget -= 1
        progress["calls_used_today"] = progress.get("calls_used_today", 0) + 1
        if processed % 50 == 0:
            _save_now()  # 중간에 프로세스가 죽어도 여기까지는 보존되도록 주기적으로 저장
        time.sleep(0.15)  # 공공데이터포털에 과도한 연속 호출을 보내지 않기 위한 최소 텀

    _save_now()
    # "포기"한 것만 remaining 계산에서 제외한다 — MAX_RETRIES 미만으로 실패한 항목은
    # 여전히 다음 실행에서 재시도되므로 "남음"에 그대로 포함돼야 한다.
    giveup_ids = {int(k) for k, v in retry_counts.items() if v >= MAX_RETRIES}

    # DB에도 이번에 갱신된 단지들의 업종 필드를 반영 (실행 중인 서버는 재시작해야 메모리에 반영됨)
    try:
        import sys
        sys.path.insert(0, BASE_DIR)
        from database import SessionLocal, IndustrialPark
        db = SessionLocal()
        touched_ids = {p["id"] for p in parks if p["id"] in done_ids}
        for park in parks:
            if park["id"] not in touched_ids:
                continue
            row = db.query(IndustrialPark).filter(IndustrialPark.id == park["id"]).first()
            if row:
                row.industries = json.dumps(park.get("industries") or [], ensure_ascii=False)
        db.commit()
        db.close()
    except Exception as e:
        print(f"⚠️ DB 반영 실패(파일은 저장됨): {e}")

    total = len(parks)
    remaining = total - len(done_ids) - len(giveup_ids)
    print(f"✅ 업종 백필: 이번 실행 {processed}건 처리(성공 {updated}, 실패 {errored}) "
          f"/ 누적 완료 {len(done_ids)} / 영구제외 {len(giveup_ids)} / 전체 {total} / 남음 {remaining}")

    return {
        "processed": processed,
        "updated": updated,
        "errored": errored,
        "done_total": len(done_ids),
        "giveup_total": len(giveup_ids),
        "grand_total": total,
        "remaining": remaining,
    }
