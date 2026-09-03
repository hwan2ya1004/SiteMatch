"""
전국 산업단지 현황 참조 통계.
출처: 한국산업단지공단 「전국산업단지현황통계」 2025년 4분기(2025.12.31 기준) 공식 문서.
"단지수"는 행정 통계 기준(다부지 복합단지를 1개로 집계)이며,
industrial_parks.json/DB는 같은 문서의 상세 시트를 물리적 위치(부지) 단위로
풀어낸 것이라 이보다 많은 1,406건을 담고 있다 — 둘 다 같은 원본 문서에서 나온
실제 수치이고, 집계 단위(행정 단지 수 vs 실제 위치 수)만 다르다.
"""

NATIONAL_PARK_TOTAL = 1359

NATIONAL_PARK_BREAKDOWN = {
    "국가산단": 55,
    "일반산단": 765,
    "도시첨단산단": 54,
    "농공산단": 485,
}

# SiteMatch 서비스가 실제로 다루는 물리적 위치(부지) 수 — 위 행정 통계와는 집계 단위가 다름
SERVICE_LOCATION_TOTAL = 1406


def get_national_park_stats() -> dict:
    return {
        "total": NATIONAL_PARK_TOTAL,
        "breakdown": NATIONAL_PARK_BREAKDOWN,
        "service_location_total": SERVICE_LOCATION_TOTAL,
    }
