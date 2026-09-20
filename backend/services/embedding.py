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

from services import industry_rules

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MATCH_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """당신은 한국 산업단지 입주 컨설턴트 AI입니다.
주어진 기업 조건과 산업단지 목록을 검토하여, 각 산업단지가 이 기업에 얼마나 적합한지 평가하세요.

평가 기준과 배점(총 100점, score는 아래 배점의 합이어야 함):
1. 업종 적합성 (40점) — 기업이 선택한 업종은 통계청 한국표준산업분류(KSIC) 대분류 C(제조업)
   중분류(10~34) 기준입니다. 산업단지의 주요 업종이 기업 업종과 얼마나 연관되는지, 아래 핵심
   입지 요인을 함께 고려하세요:
   - 전자부품·통신장비, 의료·정밀·광학기기, 전기장비: 안정적 전력·용수 공급, 클린룸 인프라, 숙련 인력 밀집 지역
   - 자동차·트레일러, 기타 운송장비, 기타 기계·장비, 금속가공제품, 1차 금속, 산업용 기계·장비 수리업: 대형 화물 운송로, 배후 협력업체·부품사 밀집도
   - 석유정제품, 화학물질·화학제품, 고무·플라스틱제품, 비금속광물제품: 폐수·폐기물 처리 시설, 위험물 취급 인허가, 임해(항만) 접근성
   - 식료품·음료, 의약품: 위생·품질 관리 인프라, 냉동·냉장 물류, 상수도 수질
   - 섬유제품·의복·가죽가방신발, 목재·펄프종이·가구·기타 제품, 인쇄업, 담배: 인건비 수준, 인력 수급 용이성
2. 희망 지역 일치 여부 (25점)
3. 예산(임대료) 적합성 (15점)
4. 물류 조건 충족 여부 (10점)
5. 필요 면적 충족 여부 (5점)
6. 기업의 추가 요구사항 반영 여부 (5점)

각 단지 정보의 "조성상태"를 반드시 확인하세요. "조성중" 또는 "미개발" 단지는 즉시 입주가
불가능하므로, 다른 조건이 동일하다면 "완료" 단지보다 순위를 낮추고, 그 단지를 추천할 때는
reason에 조성상태(예: "조성중")를 반드시 언급하세요. 조성상태를 숨기고 추천하지 마세요.

반드시 아래 JSON 배열 형식으로만, 공백·줄바꿈 없이 압축해서 답변하고 다른 설명은 절대 포함하지 마세요.
[{"id":공단ID(정수),"score":총점(0~100 정수),"breakdown":{"industry":0~40,"region":0~25,"budget":0~15,"logistics":0~10,"area":0~5,"extra":0~5},"reason":"20자 이내 핵심 근거(업종별 입지 요인 위주)"}, ...]
breakdown 각 항목의 합은 score와 같아야 합니다. reason은 반드시 20자를 넘지 마세요. 목록에 있는 모든 공단에 대해 빠짐없이 항목을 반환하세요."""

# ── 폴백용 키워드 매핑 (LLM 호출 실패 시에만 사용) ──────────────────────
# 통계청 한국표준산업분류(KSIC) 대분류 C(제조업) 중분류 10~34 기준 (사용자 지정,
# http://kssc.kostat.go.kr/ksscNew_web/kssc/common/ClassificationContent.do?gubun=1&strCategoryNameCode=001
# 확인됨 — 예전엔 "제조업(D, 15~37)"이라는 옛 개정판 기준이 문서에 남아있었는데, 현재
# 통계청 기준으로는 9차 개정 이후 계속 "제조업(C, 10~34)"이므로 전부 이 기준으로 맞춤).
# 키(value)는 SiteMatchAI.html의 #f-industry 옵션 value와 반드시 일치해야 한다.
INDUSTRY_KEYWORDS = {
    "식료품 제조업": ["식품", "식료품", "육류", "수산", "유가공", "제분", "제과", "김치", "건강기능식품", "사료"],
    "음료 제조업": ["음료", "주류", "생수", "탄산", "커피"],
    "담배 제조업": ["담배", "연초"],
    "섬유제품 제조업": ["섬유", "직물", "방적", "편조", "염색", "제사"],
    "의복·모피제품 제조업": ["의복", "봉제", "패션", "모피", "액세서리"],
    "가죽·가방·신발 제조업": ["가죽", "가방", "신발", "피혁"],
    "목재·나무제품 제조업": ["목재", "합판", "나무제품", "목공"],
    "펄프·종이 제조업": ["펄프", "종이", "제지", "골판지"],
    "인쇄업": ["인쇄", "출판", "기록매체", "복제"],
    "석유정제품 제조업": ["석유정제", "코크스", "연탄"],
    "화학물질·화학제품 제조업": ["화학", "화학물질", "화학제품", "플라스틱원료", "비료", "농약", "도료", "염료"],
    "의약품 제조업": ["의약품", "제약", "바이오의약", "백신"],
    "고무·플라스틱제품 제조업": ["고무", "플라스틱", "합성수지"],
    "비금속광물제품 제조업": ["시멘트", "유리", "도자기", "요업", "콘크리트", "석회"],
    "1차 금속 제조업": ["제철", "제강", "금속제련", "비철금속", "주조"],
    "금속가공제품 제조업": ["금속가공", "판금", "단조", "금속구조재", "금속제품"],
    "전자부품·통신장비 제조업": ["전자", "전자부품", "반도체", "디스플레이", "컴퓨터", "통신장비", "영상", "음향", "IT부품", "모바일"],
    "의료·정밀·광학기기 제조업": ["의료기기", "정밀기기", "광학기기", "시계", "측정기기"],
    "전기장비 제조업": ["전기장비", "전동기", "배전반", "변압기", "전기조명", "배터리", "이차전지"],
    "기타 기계·장비 제조업": ["기계", "장비", "산업기계", "공작기계", "건설기계", "농기계"],
    "자동차·트레일러 제조업": ["자동차", "자동차부품", "트레일러", "완성차"],
    "기타 운송장비 제조업": ["조선", "선박", "철도차량", "항공기", "운송장비"],
    "가구 제조업": ["가구"],
    "기타 제품 제조업": ["귀금속", "악기", "장난감", "운동용품"],
    "산업용 기계·장비 수리업": ["기계수리", "장비수리", "설비유지보수"],
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

# 프론트엔드 "희망 지역" 선택값(정식 명칭) → industrial_parks 데이터의 시도 표기(약칭) 매핑.
# 전국산업단지현황통계 원본이 "경기/경남/충북"처럼 약칭으로 시도를 표기하기 때문에 필요하다.
REGION_TO_DATA_SIDO = {
    "서울특별시": "서울", "경기도": "경기", "인천광역시": "인천",
    "강원도": "강원", "강원특별자치도": "강원",
    "충청남도": "충남", "충청북도": "충북", "대전광역시": "대전", "세종특별자치시": "세종",
    "전라남도": "전남", "전라북도": "전북", "전북특별자치도": "전북", "광주광역시": "광주",
    "경상남도": "경남", "경상북도": "경북", "부산광역시": "부산", "대구광역시": "대구", "울산광역시": "울산",
    "제주특별자치도": "제주",
}

# LLM 프롬프트에 한 번에 넣을 수 있는 후보 상한. Groq 무료 티어 TPM(분당 토큰) 한도 안에서
# 안전하게 처리 가능한 수준으로, 전국 1,400여 개 단지를 통째로 넣으면 토큰 초과로 요청 자체가
# 거부된다(413 rate_limit_exceeded 실제 확인됨). 지역 등으로 먼저 후보를 좁힌 뒤에도 이 상한을
# 넘으면, 키워드 사전 점수로 상위 후보만 추려서 LLM에 넘긴다.
MAX_LLM_CANDIDATES = 40

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
        available_area = park.get("available_area")
        rent_per_sqm = park.get("rent_per_sqm")
        area_text = f"{available_area:,.0f}㎡" if available_area is not None else "정보없음"
        rent_text = f"{rent_per_sqm:,}원" if rent_per_sqm is not None else "정보없음"
        dev_status = park.get("dev_status") or "완료"
        # 토큰 예산(Groq 무료 티어 TPM) 안에 38개 단지를 모두 넣기 위해 "특징" 등 부가 정보는 생략
        return (
            f"- ID{park.get('id')} {park.get('name', '')}"
            f"({park.get('region') or '정보없음'} {park.get('city') or ''}) "
            f"조성상태:{dev_status} "
            f"업종:{industries} 물류:{logistics} "
            f"면적:{area_text} "
            f"임대료:{rent_text} 지원금:{subsidy}"
        )

    def _build_user_prompt(self, industry: str, size: str, area: str,
                            region: str, budget: str, logistics: str, extra: str,
                            candidates: List[Dict]) -> str:
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
        park_lines = "\n".join(self._park_to_prompt_line(p) for p in candidates)
        return f"{company_text}\n\n[산업단지 목록]\n{park_lines}"

    @staticmethod
    def _extract_json_array(text: str) -> List[Dict]:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError("응답에서 JSON 배열을 찾지 못했습니다.")
        return json.loads(match.group(0))

    def _llm_score(self, industry: str, size: str, area: str,
                    region: str, budget: str, logistics: str, extra: str,
                    candidates: List[Dict]) -> List[Dict]:
        """Groq LLM이 (사전에 좁혀진) 공단 후보 목록을 직접 분석해 점수를 매긴다."""
        if self._client is None:
            raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다.")

        user_prompt = self._build_user_prompt(industry, size, area, region, budget, logistics, extra, candidates)
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

        park_industries = park.get("industries") or []
        park_logistics = park.get("logistics") or []
        park_features = park.get("features") or []
        park_text = " ".join([
            park.get("name") or "",
            park.get("description") or "",
            park.get("type") or "",
            " ".join(park_industries),
            " ".join(park_logistics),
            " ".join(park_features),
        ])

        keywords = INDUSTRY_KEYWORDS.get(industry, [industry])
        matched_kw = sum(1 for kw in keywords if kw in park_text)
        breakdown["industry"] = min(matched_kw / max(len(keywords), 1), 1.0) * 40

        if region and region not in ("지역 무관", ""):
            breakdown["region"] = 25.0 if self._region_matches(park.get("region"), region) else 0.0
        else:
            breakdown["region"] = 15.0

        rent = park.get("rent_per_sqm") or 0
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
            avail = park.get("available_area") or 0
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

    @staticmethod
    def _region_matches(park_region: Optional[str], region_filter: str) -> bool:
        """희망 지역(정식 명칭)과 공단 데이터의 시도 표기(약칭)를 비교한다."""
        if not park_region:
            return False
        target = REGION_TO_DATA_SIDO.get(region_filter, region_filter)
        return park_region == target or target in park_region or park_region in target

    def _select_candidates(self, industry: str, size: str, area: str,
                            region: str, budget: str, logistics: str, extra: str) -> List[Dict]:
        """LLM에 넘길 후보를 MAX_LLM_CANDIDATES 이하로 좁힌다.
        1) 희망 지역이 있으면 먼저 해당 시도로 좁히고 (없으면 전국 대상 유지)
        2) 그래도 상한을 넘으면, 키워드 사전 점수(LLM 없이 계산 가능)로 상위 후보만 추린다.
        전국 1,400여 개를 한 번에 LLM에 보내면 토큰 한도 초과로 요청이 거부되기 때문에 필요한 단계."""
        pool = self.parks_data
        if region and region not in ("지역 무관", ""):
            region_pool = [p for p in pool if self._region_matches(p.get("region"), region)]
            if region_pool:
                pool = region_pool

        # 지금 입주할 수 없는 단지(조성중/미개발/가용면적 없음)와, 공식 고시문서상 선택한
        # 업종이 입주 불가로 확인된 단지는 후보에서 아예 뺀다.
        division = industry_rules.guess_division(industry)
        pool = [p for p in pool
                if self._is_available(p) and not industry_rules.is_blocked(p.get("id"), division)]

        if len(pool) <= MAX_LLM_CANDIDATES:
            return pool

        scored = [
            (sum(self._keyword_score_park(p, industry, size, area, region, budget, logistics, extra).values()), p)
            for p in pool
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        # 키워드 점수만으로 상위 40개를 뽑으면, "지금 입주 가능한"(완료+가용면적>0) 단지가
        # 소수인 지역·업종 조합에서는 그 단지들이 키워드 매칭이 약하다는 이유로 전부 40위
        # 밖으로 밀려나 LLM이 아예 보지도 못하는 문제가 실제로 있었다(경기도 217개 중
        # 입주가능 10개가 후보 40개에서 전부 탈락해, 결과 5개가 전부 입주불가로 나온 사례).
        # 입주 가능 단지는 키워드 점수와 무관하게 우선 포함시켜서 이 문제를 근본적으로 막는다.
        available_scored = [(s, p) for s, p in scored if self._is_available(p)]
        unavailable_scored = [(s, p) for s, p in scored if not self._is_available(p)]

        selected = [p for _, p in available_scored[:MAX_LLM_CANDIDATES]]
        remaining_slots = MAX_LLM_CANDIDATES - len(selected)
        if remaining_slots > 0:
            selected += [p for _, p in unavailable_scored[:remaining_slots]]
        return selected

    def search(self, industry: str, size: str, area: str,
               region: str, budget: str, logistics: str, extra: str,
               top_k: int = 5) -> List[Dict]:
        """기업 조건으로 공단 검색. 지역/키워드로 후보를 먼저 좁힌 뒤,
        Groq LLM이 그 후보만 직접 분석하며, 실패 시 키워드 매칭으로 폴백."""
        if not self.parks_data:
            raise ValueError("공단 데이터가 로드되지 않았습니다.")

        candidates = self._select_candidates(industry, size, area, region, budget, logistics, extra)

        try:
            llm_scores = self._llm_score(industry, size, area, region, budget, logistics, extra, candidates)
            score_map = {item["id"]: item for item in llm_scores}
            results = []
            for park in candidates:
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
            for park in candidates:
                breakdown = self._keyword_score_park(
                    park, industry, size, area, region, budget, logistics, extra
                )
                total = round(sum(breakdown.values()), 1)
                results.append({"park": park, "score": total, "reason": "", "breakdown": breakdown})

        self._apply_availability_penalty(results)
        results.sort(key=lambda x: x["score"], reverse=True)

        # 입주 가능한 단지만 반환한다. 조건에 맞는 곳이 top_k보다 적으면 적은 대로,
        # 없으면 빈 목록을 돌려준다(입주 불가 단지로 채우지 않는다).
        division = industry_rules.guess_division(industry)
        final = [r for r in results if self._is_available(r["park"])][:top_k]
        for r in final:
            status, note = industry_rules.check(r["park"].get("id"), division)
            if note:
                r["reason"] = f"{r['reason']} · {note}" if r.get("reason") else note
        return final

    @staticmethod
    def _is_available(park: Dict) -> bool:
        dev_status = park.get("dev_status") or "완료"
        avail = park.get("available_area")
        return dev_status == "완료" and bool(avail) and avail > 0

    @staticmethod
    def _apply_availability_penalty(results: List[Dict]) -> None:
        """지금 당장 입주할 수 없는 단지(조성중/미개발, 또는 완공됐지만 가용면적 0)는
        업종·지역 궁합 점수와 무관하게 후순위로 내린다.

        원래는 LLM 프롬프트에 "조성중/미개발이면 순위를 낮춰라"라고만 지시했는데,
        LLM이 그 지시를 안 따르고 "조성중" 단지를 1위로 추천하는 사례가 실사용 중
        실제로 발생했다 — 자연어 지시만 믿지 말고 코드에서 직접 감점해야 한다.
        완공됐는데(dev_status=='완료') 가용면적이 0인 경우(이미 분양·입주가 끝나
        빈 자리가 없음)도 같은 이유로 동일하게 처리한다."""
        for r in results:
            park = r["park"]
            dev_status = park.get("dev_status") or "완료"
            avail = park.get("available_area")
            note = None
            if dev_status != "완료":
                r["score"] = round(r["score"] * 0.5, 1)
                note = f"조성상태: {dev_status} (아직 입주 불가)"
            elif not avail or avail <= 0:
                r["score"] = round(r["score"] * 0.6, 1)
                note = "현재 가용면적 없음(공실 재확인 필요)"
            if note and "조성상태" not in (r.get("reason") or "") and "가용면적 없음" not in (r.get("reason") or ""):
                r["reason"] = f"{r['reason']} · {note}" if r.get("reason") else note


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
