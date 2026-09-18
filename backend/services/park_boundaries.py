"""
산업단지 경계 폴리곤 데이터 (국토교통부 "산업단지 경계도면" 고시, EPSG:3857 -> WGS84 변환,
DB 단지명과 매칭 완료) 조회.

backend/data/park_boundaries.json 은 {park_id(str): [[[lng, lat], ...], ...]} 형태로,
각 단지의 폴리곤을 이루는 좌표 링(ring) 목록이다. 전국 1,340개 경계 중 DB 이름과
지역/시군구까지 일치가 확인된 1,027개 단지만 포함한다 — 동명이지만 다른 지역인
단지(예: "하리" 강원 양구군 vs 경남 창녕군)는 잘못 매칭될 위험이 있어 자동 매칭에서
제외했다(실데이터만 쓰고 추측하지 않는다는 원칙).

예외: 가남신해1~5일반산업단지(id 365~369)는 2025.12.31 여주시 고시로 신규
지정된 단지라 위 국토교통부 경계도면(2025.5월 기준)에 아직 없다. 이 5곳은
VWorld 지적도 API(정밀 필지 경계)에 접근 권한이 없어서, 대신 각 단지 고시문의
"가구 및 획지" 표에 나온 실제 지번들을 VWorld 주소검색 API로 지오코딩하고
그 점들의 convex hull(볼록 껍질)을 그린 것이다 — 정밀한 지적 경계선이 아니라
실제 지번 좌표들을 감싸는 근사 다각형이니, 나중에 VWorld 데이터 API 권한이
생기면 진짜 필지 경계로 교체하는 게 좋다.
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
