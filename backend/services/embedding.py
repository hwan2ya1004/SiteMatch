"""
키워드 기반 AI 매칭 엔진 (Render 무료 플랜 최적화)
sentence-transformers / FAISS 없이 키워드 스코어링으로 동작
"""
import json
import os
from typing import List, Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 업종 매핑 (한국어 → 영문 키워드 확장)
INDUSTRY_KEYWORDS = {
    "전자·반도체": ["전자", "반도체", "디스플레이", "IT부품", "모바일", "PCB"],
    "자동차·부품": ["자동차", "자동차부품", "방위산업", "기계"],
    "기계·금속 장비": ["기계", "금속", "장비", "로봇", "자동화"],
    "화학·소재": ["화학", "석유화학", "소재", "플라스틱"],
    "식품·음료": ["식품", "음료", "농산물"],
    "섬유·의류": ["섬유", "의류", "패션"],
    "바이오·의료기기": ["바이오", "의료기기", "제약", "화학"],
    "물류·유통": ["물류", "유통", "창고"],
    "로봇·스마트팩토리": ["로봇", "자동화", "스마트팩토리", "AI", "기계"],
    "기타 제조업": ["제조", "기계", "금속"],
}

# 지역 매핑
REGION_MAP = {
    "경기도": ["경기도", "경기 안산", "경기 평택", "경기 화성"],
    "인천광역시": ["인천광역시", "인천 남동"],
    "경상남도": ["경상남도", "경남 창원"],
    "경상북도": ["경상북도", "경북 구미"],
    "충청남도": ["충청남도", "충남 천안"],
    "충청북도": ["충청북도", "충북 청주"],
    "전라남도": ["전라남도"],
    "전라북도": ["전라북도", "전북 군산"],
    "강원도": ["강원도"],
    "부산광역시": ["부산광역시", "부산 사하"],
    "대구광역시": ["대구광역시", "대구 달서"],
    "울산광역시": ["울산광역시", "울산 동구"],
    "광주광역시": ["광주광역시", "광주 북구"],
}

# 면적 조건 매핑 (㎡)
AREA_MAP = {
    "330㎡ 미만 (100평)": (0, 330),
    "330~1,000㎡ (100~300평)": (330, 1000),
    "1,000~3,300㎡ (300~1,000평)": (1000, 3300),
    "3,300~10,000㎡ (1,000~3,000평)": (3300, 10000),
    "10,000㎡ 이상 (3,000평+)": (10000, float("inf")),
}

# 예산 조건 매핑 (임대료 원/㎡/월 기준)
BUDGET_MAP = {
    "500만원 미만": 20000,
    "500~1,000만원": 30000,
    "1,000~3,000만원": 50000,
    "3,000만원 이상": 999999,
}


class EmbeddingService:
    def __init__(self, api_key: str = None):
        self.parks_data: List[Dict] = []

    def build_index(self, parks: List[Dict]):
        """공단 데이터 로드 (키워드 방식은 인덱스 불필요)"""
        self.parks_data = [
            p if isinstance(p, dict) else {c.name: getattr(p, c.name) for c in p.__table__.columns}
            for p in parks
        ]
        # JSON 필드 파싱
        for park in self.parks_data:
            for field in ["industries", "logistics", "features"]:
                if isinstance(park.get(field), str):
                    try:
                        park[field] = json.loads(park[field])
                    except Exception:
                        park[field] = []
        print(f"✅ 매칭 엔진 초기화 완료 ({len(self.parks_data)}개 공단, 키워드 방식)")

    def load_index(self) -> bool:
        """키워드 방식은 별도 인덱스 파일 불필요 — 항상 False 반환해 build_index 호출 유도"""
        return False

    def _score_park(self, park: Dict, industry: str, size: str, area: str,
                    region: str, budget: str, logistics: str, extra: str) -> float:
        """공단 하나에 대한 키워드 매칭 점수 계산 (0.0 ~ 1.0)"""
        score = 0.0
        max_score = 0.0

        park_industries = park.get("industries", [])
        park_logistics = park.get("logistics", [])
        park_features = park.get("features", [])
        park_region = park.get("region", "") + " " + park.get("city", "")
        park_text = " ".join([
            park.get("name", ""),
            park.get("description", ""),
            park.get("type", ""),
            " ".join(park_industries),
            " ".join(park_logistics),
            " ".join(park_features),
        ])

        # ── 1. 업종 매칭 (가중치 40%) ──────────────────────────────
        max_score += 40
        keywords = INDUSTRY_KEYWORDS.get(industry, [industry])
        matched_kw = sum(1 for kw in keywords if kw in park_text)
        industry_score = min(matched_kw / max(len(keywords), 1), 1.0) * 40
        score += industry_score

        # ── 2. 지역 매칭 (가중치 25%) ──────────────────────────────
        max_score += 25
        if region and region not in ("지역 무관", ""):
            region_keywords = REGION_MAP.get(region, [region])
            if any(kw in park_region for kw in region_keywords):
                score += 25
        else:
            score += 15  # 지역 무관이면 기본 점수 부여

        # ── 3. 예산(임대료) 매칭 (가중치 20%) ─────────────────────
        max_score += 20
        rent = park.get("rent_per_sqm", 0)
        if budget and budget not in ("무관", ""):
            max_rent = BUDGET_MAP.get(budget, 999999)
            if rent <= max_rent:
                score += 20
            elif rent <= max_rent * 1.3:
                score += 10  # 30% 초과까지는 부분 점수
        else:
            score += 15  # 예산 무관이면 기본 점수

        # ── 4. 물류 조건 매칭 (가중치 10%) ────────────────────────
        max_score += 10
        if logistics and logistics not in ("무관", ""):
            if logistics in park_text or any(logistics in lg for lg in park_logistics):
                score += 10
            else:
                score += 3
        else:
            score += 7

        # ── 5. 가용 면적 매칭 (가중치 5%) ─────────────────────────
        max_score += 5
        if area and area in AREA_MAP:
            min_area, max_area = AREA_MAP[area]
            avail = park.get("available_area", 0)
            if avail >= min_area:
                score += 5
            elif avail > 0:
                score += 2

        return score / max_score  # 0.0 ~ 1.0 정규화

    def search(self, industry: str, size: str, area: str,
               region: str, budget: str, logistics: str, extra: str,
               top_k: int = 5) -> List[Dict]:
        """기업 조건으로 공단 검색 (키워드 스코어링)"""
        if not self.parks_data:
            raise ValueError("공단 데이터가 로드되지 않았습니다.")

        results = []
        for park in self.parks_data:
            raw_score = self._score_park(
                park, industry, size, area, region, budget, logistics, extra
            )
            results.append({
                "park": park,
                "score": round(raw_score * 100, 1),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


# 싱글톤 인스턴스
_embedding_service: EmbeddingService = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    return _embedding_service


def init_embedding_service(api_key: str, parks: List[Dict]) -> EmbeddingService:
    global _embedding_service
    _embedding_service = EmbeddingService(api_key)
    # 키워드 방식은 load_index()가 항상 False → build_index() 호출
    if not _embedding_service.load_index():
        _embedding_service.build_index(parks)
    return _embedding_service
