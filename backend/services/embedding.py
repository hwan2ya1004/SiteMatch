"""
LLM 기반 AI 매칭 엔진 (Groq openai/gpt-oss-120b)
기업 조건과 공단 데이터를 LLM에게 직접 분석시켜 적합도 점수를 산출한다.
로컬 임베딩 모델(sentence-transformers)이나 FAISS 없이 API 호출만으로 동작하므로
Render 무료 플랜의 메모리 제약에서도 안정적으로 실행된다.
LLM 호출이 실패하면 규칙 기반 키워드 스코어링으로 자동 폴백한다.
"""
import json
import os
import re
from typing import List, Dict, Any, Optional

from groq import Groq

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MATCH_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """당신은 한국 산업단지 입주 컨설턴트 AI입니다.
주어진 기업 조건과 산업단지 목록을 검토하여, 각 산업단지가 이 기업에 얼마나 적합한지 평가하세요.

평가 기준과 배점(총 100점, score는 아래 배점의 합이어야 함):
1. 업종 적합성 (40점) — 산업단지의 주요 업종이 기업 업종과 얼마나 연관되는지. 아래 업종별 핵심 입지 요인을 반드시 함께 고려하세요:
   - 전자·반도체: 안정적 전력·용수 공급, 클린룸 인프라, 숙련 인력 밀집 지역(용인·수원·평택·이천 등)
   - 자동차·부품, 기계·금속 장비, 로봇·스마트팩토리: 대형 화물 운송로, 배후 협력업체·부품사 밀집도
   - 화학·소재: 폐수·폐기물 처리 시설, 위험물 취급 인허가, 임해(항만) 접근성
   - 식품·음료, 바이오·의료기기: 위생·품질 관리 인프라, 냉동·냉장 물류, 상수도 수질
   - 물류·유통: 고속도로 IC·항만·공항 접근성
   - 섬유·의류, 기타 제조업: 인건비 수준, 인력 수급 용이성
2. 희망 지역 일치 여부 (25점)
3. 예산(임대료) 적합성 (15점)
4. 물류 조건 충족 여부 (10점)
5. 필요 면적 충족 여부 (5점)
6. 기업의 추가 요구사항 반영 여부 (5점)

반드시 아래 JSON 배열 형식으로만, 공백·줄바꿈 없이 압축해서 답변하고 다른 설명은 절대 포함하지 마세요.
[{"id":공단ID(정수),"score":총점(0~100 정수),"breakdown":{"industry":0~40,"region":0~25,"budget":0~15,"logistics":0~10,"area":0~5,"extra":0~5},"reason":"20자 이내 핵심 근거(업종별 입지 요인 위주)"}, ...]
breakdown 각 항목의 합은 score와 같아야 합니다. reason은 반드시 20자를 넘지 마세요. 목록에 있는 모든 공단에 대해 빠짐없이 항목을 반환하세요."""

# ── 폴백용 키워드 매핑 (LLM 호출 실패 시에만 사용) ──────────────────────
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

AREA_MAP = {
    "330㎡ 미만 (100평)": (0, 330),
    "330~1,000㎡ (100~300평)": (330, 1000),
    "1,000~3,300㎡ (300~1,000평)": (1000, 3300),
    "3,300~10,000㎡ (1,000~3,000평)": (3300, 10000),
    "10,000㎡ 이상 (3,000평+)": (10000, float("inf")),
}

BUDGET_MAP = {
    "500만원 미만": 20000,
    "500~1,000만원": 30000,
    "1,000~3,000만원": 50000,
    "3,000만원 이상": 999999,
}


class EmbeddingService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self._client: Optional[Groq] = Groq(api_key=api_key) if api_key else None
        self.parks_data: List[Dict] = []

    def build_index(self, parks: List[Dict]):
        """공단 데이터 로드 (LLM 방식은 별도 인덱스 구축 불필요)"""
        self.parks_data = [
            p if isinstance(p, dict) else {c.name: getattr(p, c.name) for c in p.__table__.columns}
            for p in parks
        ]
        for park in self.parks_data:
            for field in ["industries", "logistics", "features"]:
                if isinstance(park.get(field), str):
                    try:
                        park[field] = json.loads(park[field])
                    except Exception:
                        park[field] = []
        print(f"✅ 매칭 엔진 초기화 완료 ({len(self.parks_data)}개 공단, LLM 분석 방식)")

    def load_index(self) -> bool:
        """LLM 방식은 별도 인덱스 파일 불필요 — 항상 False 반환해 build_index 호출 유도"""
        return False

    # ── LLM 기반 매칭 ────────────────────────────────────────────────
    def _park_to_prompt_line(self, park: Dict) -> str:
        industries = ", ".join(park.get("industries") or []) or "정보없음"
        logistics = ", ".join(park.get("logistics") or []) or "정보없음"
        subsidy = park.get("subsidy") or "정보없음"
        if len(subsidy) > 25:
            subsidy = subsidy[:25] + "…"
        # 토큰 예산(Groq 무료 티어 TPM) 안에 38개 단지를 모두 넣기 위해 "특징" 등 부가 정보는 생략
        return (
            f"- ID{park.get('id')} {park.get('name', '')}"
            f"({park.get('region', '')} {park.get('city', '')}) "
            f"업종:{industries} 물류:{logistics} "
            f"면적:{park.get('available_area', 0):,.0f}㎡ "
            f"임대료:{park.get('rent_per_sqm', 0):,}원 지원금:{subsidy}"
        )

    def _build_user_prompt(self, industry: str, size: str, area: str,
                            region: str, budget: str, logistics: str, extra: str) -> str:
        company_text = (
            f"[기업 조건]\n"
            f"업종: {industry}\n"
            f"종업원 수: {size}\n"
            f"필요 면적: {area}\n"
            f"희망 지역: {region or '지역 무관'}\n"
            f"월 예산(임대료): {budget or '무관'}\n"
            f"물류 조건: {logistics or '무관'}\n"
            f"추가 요구사항: {extra or '없음'}"
        )
        park_lines = "\n".join(self._park_to_prompt_line(p) for p in self.parks_data)
        return f"{company_text}\n\n[산업단지 목록]\n{park_lines}"

    @staticmethod
    def _extract_json_array(text: str) -> List[Dict]:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError("응답에서 JSON 배열을 찾지 못했습니다.")
        return json.loads(match.group(0))

    def _llm_score(self, industry: str, size: str, area: str,
                    region: str, budget: str, logistics: str, extra: str) -> List[Dict]:
        """Groq LLM이 공단 목록을 직접 분석해 점수를 매긴다."""
        if self._client is None:
            raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다.")

        user_prompt = self._build_user_prompt(industry, size, area, region, budget, logistics, extra)
        completion = self._client.chat.completions.create(
            model=MATCH_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=3400,
            reasoning_effort="low",  # gpt-oss는 추론 모델 — effort를 낮추지 않으면 토큰 예산을 "생각"에 다 씀
        )
        raw = completion.choices[0].message.content
        scored = self._extract_json_array(raw)

        results = []
        for item in scored:
            try:
                results.append({
                    "id": int(item["id"]),
                    "score": max(0.0, min(100.0, float(item["score"]))),
                    "reason": str(item.get("reason", "")),
                    "breakdown": self._sanitize_breakdown(item.get("breakdown")),
                })
            except (KeyError, TypeError, ValueError):
                continue
        if not results:
            raise ValueError("LLM 응답에서 유효한 점수를 파싱하지 못했습니다.")
        return results

    @staticmethod
    def _sanitize_breakdown(raw: Any) -> Dict[str, float]:
        """LLM이 반환한 breakdown을 배점 범위 안으로 보정한다."""
        caps = {"industry": 40, "region": 25, "budget": 15, "logistics": 10, "area": 5, "extra": 5}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for key, cap in caps.items():
            try:
                out[key] = max(0.0, min(float(cap), float(raw.get(key, 0))))
            except (TypeError, ValueError):
                out[key] = 0.0
        return out

    # ── 폴백: 규칙 기반 키워드 스코어링 (LLM과 동일한 100점 배점 체계) ──
    def _keyword_score_park(self, park: Dict, industry: str, size: str, area: str,
                             region: str, budget: str, logistics: str, extra: str) -> Dict[str, float]:
        breakdown = {"industry": 0.0, "region": 0.0, "budget": 0.0, "logistics": 0.0, "area": 0.0, "extra": 0.0}

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

        keywords = INDUSTRY_KEYWORDS.get(industry, [industry])
        matched_kw = sum(1 for kw in keywords if kw in park_text)
        breakdown["industry"] = min(matched_kw / max(len(keywords), 1), 1.0) * 40

        if region and region not in ("지역 무관", ""):
            region_keywords = REGION_MAP.get(region, [region])
            breakdown["region"] = 25.0 if any(kw in park_region for kw in region_keywords) else 0.0
        else:
            breakdown["region"] = 15.0

        rent = park.get("rent_per_sqm", 0)
        if budget and budget not in ("무관", ""):
            max_rent = BUDGET_MAP.get(budget, 999999)
            if rent <= max_rent:
                breakdown["budget"] = 15.0
            elif rent <= max_rent * 1.3:
                breakdown["budget"] = 7.0
        else:
            breakdown["budget"] = 11.0

        if logistics and logistics not in ("무관", ""):
            if logistics in park_text or any(logistics in lg for lg in park_logistics):
                breakdown["logistics"] = 10.0
            else:
                breakdown["logistics"] = 3.0
        else:
            breakdown["logistics"] = 7.0

        if area and area in AREA_MAP:
            min_area, _ = AREA_MAP[area]
            avail = park.get("available_area", 0)
            if avail >= min_area:
                breakdown["area"] = 5.0
            elif avail > 0:
                breakdown["area"] = 2.0
        else:
            breakdown["area"] = 3.0

        if extra and extra.strip():
            breakdown["extra"] = 5.0 if extra.strip() in park_text else 1.0
        else:
            breakdown["extra"] = 3.0

        return breakdown

    def search(self, industry: str, size: str, area: str,
               region: str, budget: str, logistics: str, extra: str,
               top_k: int = 5) -> List[Dict]:
        """기업 조건으로 공단 검색. Groq LLM이 직접 분석하며, 실패 시 키워드 매칭으로 폴백."""
        if not self.parks_data:
            raise ValueError("공단 데이터가 로드되지 않았습니다.")

        try:
            llm_scores = self._llm_score(industry, size, area, region, budget, logistics, extra)
            score_map = {item["id"]: item for item in llm_scores}
            results = []
            for park in self.parks_data:
                item = score_map.get(park.get("id"))
                if item:
                    results.append({
                        "park": park,
                        "score": round(item["score"], 1),
                        "reason": item["reason"],
                        "breakdown": item.get("breakdown", {}),
                    })
                else:
                    results.append({"park": park, "score": 0.0, "reason": "", "breakdown": {}})
        except Exception as e:
            print(f"⚠️ LLM 매칭 실패, 키워드 매칭으로 대체: {e}")
            results = []
            for park in self.parks_data:
                breakdown = self._keyword_score_park(
                    park, industry, size, area, region, budget, logistics, extra
                )
                total = round(sum(breakdown.values()), 1)
                results.append({"park": park, "score": total, "reason": "", "breakdown": breakdown})

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
    if not _embedding_service.load_index():
        _embedding_service.build_index(parks)
    return _embedding_service
