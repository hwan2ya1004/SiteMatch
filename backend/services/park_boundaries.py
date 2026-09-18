"""
산업단지 경계 폴리곤 데이터 (국토교통부 "산업단지 경계도면" 고시, EPSG:3857 -> WGS84 변환,
DB 단지명과 매칭 완료) 조회.

backend/data/park_boundaries.json 은 {park_id(str): [[[lng, lat], ...], ...]} 형태로,
각 단지의 폴리곤을 이루는 좌표 링(ring) 목록이다. 전국 1,340개 경계 중 DB 이름과
지역/시군구까지 일치가 확인된 1,027개 단지만 포함한다 — 동명이지만 다른 지역인
단지(예: "하리" 강원 양구군 vs 경남 창녕군)는 잘못 매칭될 위험이 있어 자동 매칭에서
제외했다(실데이터만 쓰고 추측하지 않는다는 원칙).
"""
import json
import os
from functools import lru_cache
from typing import List, Optional

_SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SERVICES_DIR)
BOUNDARIES_PATH = os.path.join(_BACKEND_DIR, "data", "park_boundaries.json")


@lru_cache(maxsize=1)
def _load_all() -> dict:
    if not os.path.isfile(BOUNDARIES_PATH):
        return {}
    with open(BOUNDARIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_park_boundary(park_id: int) -> Optional[List[List[List[float]]]]:
    """해당 단지의 경계 폴리곤(링 목록, 각 링은 [lng, lat] 좌표 배열)을 반환한다.
    데이터가 없으면 None."""
    return _load_all().get(str(park_id))
