"""
HuggingFace 임베딩 + FAISS 벡터 검색 기반 AI 매칭 엔진
임베딩: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (로컬, 무료)
"""
import json
import os
import pickle
import numpy as np
from typing import List, Dict, Any

from sentence_transformers import SentenceTransformer
import faiss

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(BASE_DIR, "data", "faiss_index.pkl")

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


class EmbeddingService:
    def __init__(self, api_key: str = None):
        # HuggingFace 로컬 임베딩 (무료, 다국어 지원)
        self._model = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.index = None
        self.park_ids = []
        self.parks_data = []

    def _embed_text(self, text: str) -> List[float]:
        """단일 텍스트 임베딩"""
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def _embed_query(self, text: str) -> List[float]:
        """쿼리 텍스트 임베딩"""
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def _park_to_text(self, park: Dict) -> str:
        """공단 데이터를 임베딩용 텍스트로 변환"""
        industries = park.get("industries", [])
        if isinstance(industries, str):
            industries = json.loads(industries)
        logistics = park.get("logistics", [])
        if isinstance(logistics, str):
            logistics = json.loads(logistics)
        features = park.get("features", [])
        if isinstance(features, str):
            features = json.loads(features)

        return (
            f"산업단지명: {park['name']}. "
            f"위치: {park['city']} ({park['region']}). "
            f"유형: {park.get('type', '')}. "
            f"주요 업종: {', '.join(industries)}. "
            f"물류 조건: {', '.join(logistics)}. "
            f"특징: {', '.join(features)}. "
            f"가용 면적: {park.get('available_area', 0):,.0f}㎡. "
            f"공실률: {park.get('vacancy_rate', 0)}%. "
            f"임대료: {park.get('rent_per_sqm', 0):,}원/㎡/월. "
            f"설명: {park.get('description', '')}. "
            f"지원금: {park.get('subsidy', '')}."
        )

    def _query_to_text(self, industry: str, size: str, area: str,
                       region: str, budget: str, logistics: str, extra: str) -> str:
        """기업 조건을 임베딩용 쿼리 텍스트로 변환"""
        keywords = INDUSTRY_KEYWORDS.get(industry, [industry])
        return (
            f"업종: {industry} ({', '.join(keywords)}). "
            f"종업원 수: {size}. "
            f"필요 면적: {area}. "
            f"희망 지역: {region or '지역 무관'}. "
            f"월 예산: {budget or '무관'}. "
            f"물류 조건: {logistics or '무관'}. "
            f"추가 요구사항: {extra or '없음'}."
        )

    def build_index(self, parks: List[Dict]):
        """공단 데이터로 FAISS 인덱스 구축"""
        print("🔄 FAISS 인덱스 구축 중...")
        self.parks_data = parks
        self.park_ids = [p["id"] if isinstance(p, dict) else p.id for p in parks]

        texts = [self._park_to_text(p if isinstance(p, dict) else p.__dict__) for p in parks]
        embeddings = []
        for i, text in enumerate(texts):
            emb = self._embed_text(text)
            embeddings.append(emb)
            print(f"  임베딩 {i+1}/{len(texts)}: {parks[i]['name'] if isinstance(parks[i], dict) else parks[i].name}")

        vectors = np.array(embeddings, dtype=np.float32)
        # L2 정규화 (코사인 유사도용)
        faiss.normalize_L2(vectors)

        dim = vectors.shape[1]
        self.index = faiss.IndexFlatIP(dim)  # Inner Product = 코사인 유사도 (정규화 후)
        self.index.add(vectors)

        # 인덱스 저장
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "wb") as f:
            pickle.dump({
                "index": faiss.serialize_index(self.index),
                "park_ids": self.park_ids,
                "parks_data": self.parks_data,
            }, f)
        print(f"✅ FAISS 인덱스 구축 완료 ({len(parks)}개 공단)")

    def load_index(self) -> bool:
        """저장된 FAISS 인덱스 로드"""
        if not os.path.exists(INDEX_PATH):
            return False
        with open(INDEX_PATH, "rb") as f:
            data = pickle.load(f)
        self.index = faiss.deserialize_index(data["index"])
        self.park_ids = data["park_ids"]
        self.parks_data = data["parks_data"]
        print(f"✅ FAISS 인덱스 로드 완료 ({len(self.park_ids)}개 공단)")
        return True

    def search(self, industry: str, size: str, area: str,
               region: str, budget: str, logistics: str, extra: str,
               top_k: int = 5) -> List[Dict]:
        """기업 조건으로 유사 공단 검색"""
        if self.index is None:
            raise ValueError("FAISS 인덱스가 로드되지 않았습니다.")

        query_text = self._query_to_text(industry, size, area, region, budget, logistics, extra)
        query_emb = self._embed_query(query_text)
        query_vec = np.array([query_emb], dtype=np.float32)
        faiss.normalize_L2(query_vec)

        scores, indices = self.index.search(query_vec, min(top_k, len(self.park_ids)))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            park = self.parks_data[idx]
            if isinstance(park, dict):
                park_dict = park.copy()
            else:
                park_dict = {c.name: getattr(park, c.name) for c in park.__table__.columns}

            # JSON 필드 파싱
            for field in ["industries", "logistics", "features"]:
                if isinstance(park_dict.get(field), str):
                    try:
                        park_dict[field] = json.loads(park_dict[field])
                    except Exception:
                        park_dict[field] = []

            # 지역 필터 (희망 지역이 있을 경우 점수 보정)
            similarity_score = float(score)
            if region and region != "지역 무관":
                region_keywords = REGION_MAP.get(region, [region])
                park_region = park_dict.get("region", "") + park_dict.get("city", "")
                if any(kw in park_region for kw in region_keywords):
                    similarity_score = min(1.0, similarity_score * 1.15)  # 지역 일치 보너스

            # 예산 필터 (임대료 기준)
            rent = park_dict.get("rent_per_sqm", 0)
            if budget and budget != "무관":
                budget_ok = _check_budget(budget, rent)
                if not budget_ok:
                    similarity_score *= 0.7  # 예산 초과 시 점수 감소

            results.append({
                "park": park_dict,
                "score": round(similarity_score * 100, 1),
            })

        # 점수 기준 정렬
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


def _check_budget(budget: str, rent_per_sqm: int) -> bool:
    """예산 조건 체크"""
    budget_map = {
        "500만원 미만": 20000,
        "500~1,000만원": 30000,
        "1,000~3,000만원": 50000,
        "3,000만원 이상": 999999,
    }
    max_rent = budget_map.get(budget, 999999)
    return rent_per_sqm <= max_rent


# 싱글톤 인스턴스
_embedding_service: EmbeddingService = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    return _embedding_service


def init_embedding_service(api_key: str, parks: List[Dict]) -> EmbeddingService:
    global _embedding_service
    _embedding_service = EmbeddingService(api_key)
    # 저장된 인덱스 로드 시도, 없으면 새로 구축
    if not _embedding_service.load_index():
        _embedding_service.build_index(parks)
    return _embedding_service
