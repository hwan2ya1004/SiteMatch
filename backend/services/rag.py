"""
LangChain RAG + Groq 기반 챗봇 서비스
임베딩/FAISS 없이 subsidy_docs.txt를 직접 컨텍스트로 활용 (Render 무료 플랜 최적화)
"""
import os
from typing import List, AsyncGenerator, Dict, Optional

from groq import Groq
from langchain_groq import ChatGroq

from services.park_docs import get_park_documents_text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_PATH = os.path.join(BASE_DIR, "data", "subsidy_docs.txt")

# llama-3.3-70b-versatile는 Groq에서 서비스 종료되어 대체 모델로 전환 (2026-08 확인)
CHAT_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """당신은 한국 산업단지 입주 전문 상담 AI 'SiteMatch AI'입니다.
산업단지 입주와 관련된 지원금, 인허가 절차, 세금 혜택, 입지 추천 등에 대해 
친절하고 구체적으로 답변하세요.

다음 규칙을 따르세요:
1. 참고 문서에 실제로 적힌 제도명·금액만 사용하세요. 문서에 없는 금액·비율·조건은 절대로
   지어내지 마세요. 문서에 "확인 필요" 또는 "확인되지 않음"이라고 되어 있으면, 그 사실 그대로
   "정확한 금액은 확인되지 않았습니다"라고 답하세요 — 그럴듯한 숫자를 새로 만들어 채우는 것이
   가장 나쁜 답변입니다.
2. 절차·조건처럼 여러 단계로 나뉘는 질문에는 1번부터 번호를 매겨 순서대로 자세히 설명하세요.
   길이를 줄이려고 정보를 생략하지 말고, 확인된 내용은 빠짐없이 구조적으로 안내하세요.
3. 한국어로만 답변하세요
4. 관리기관 전화번호 등 개별 담당자 연락처는 안내하지 마세요 (기관명 자체는 참고 문서에 있으면
   언급해도 됩니다). 답변은 질문에 대한 내용으로 끝내세요 — "문의하세요", "확인해드립니다",
   "신청하시면 도와드립니다" 같은 안내·유도성 마무리 문장을 답변 끝에 덧붙이지 마세요. 아는 내용은
   바로 답하고, 모르는 내용은 모른다고 답하면 그걸로 끝입니다.
5. 참고 문서에 구체적인 내용(금액·조건·절차 등)이 없는 질문이라도, 무조건 "해당 정보는
   없습니다"로 끝내지 마세요. 용어의 뜻이나 일반 개념(예: "행정사가 뭐야?", "공장등록이 뭐야?")처럼
   시간이 지나도 안 바뀌는 정의·개념 수준이면 아는 대로 설명해주고, 그 다음에 "다만 구체적인
   금액·조건·절차는 확인되지 않았습니다"라고 이어가세요. 정말 아무 실마리도 없는 질문에서만
   "해당 정보는 없습니다"라고 답하세요.
6. 5번의 "일반 개념 설명"은 절대로 교통편·경로·요금·시간표·전화번호·주소·거리·날짜처럼 실시간으로
   바뀌고 사실 확인이 필요한 "운영 정보"에는 적용하지 마세요. 예를 들어 "OO에서 OO산단까지
   어떻게 가?"처럼 구체적 이동 경로·버스터미널명·요금·소요시간을 묻는 질문에 참고 문서·실측 데이터에
   없는 답을 그럴듯하게 만들어내면 절대 안 됩니다 — 이런 종류는 100% 사실이거나 100% 지어낸 것 중
   하나이지 "일반 상식"이 아닙니다. 이 경우 "정확한 교통편 정보는 확인되지 않았습니다. 관리기관
   또는 지도 서비스에서 확인해보시기 바랍니다"처럼 솔직하게 답하세요.
7. 아래 참고 문서를 활용하여 정확한 정보를 제공하세요
8. 참고 문서 맨 앞에 "[단지명 실측 데이터]" 블록이 있다면, 그건 SiteMatch DB에서 방금 조회한
   실제 수치입니다 — 질문한 단지에 대한 답변은 반드시 이 블록의 수치를 그대로 인용하세요.
   이 블록이 없다면, 또는 블록은 있어도 물어본 항목(예: 가동률)이 그 안에 안 적혀 있다면,
   절대로 다른 수치(면적 등)로부터 계산·추정해서 만들어내지 말고 "해당 정보는 없습니다"라고
   솔직히 답하세요. 없는 수치를 그럴듯하게 계산해서 답하는 것이 가장 나쁜 답변입니다.
9. "[단지명 관리기관 공식 고시문서 발췌]" 블록이 있다면, 이건 해당 관리기관(시청 등)이 실제로
   발행한 관리기본계획·지형도면 고시문 원문 일부입니다 — 입주업체 목록, 업종별 배치, 입주제한
   업종, 추진경위 등을 물으면 이 블록을 근거로 답하세요. 이 블록이 없다면 그 단지의 공식
   고시문서를 아직 확보하지 못한 것뿐이니, 없다고 솔직히 답하고 DB 실측 데이터로만 답하세요.
10. 정확도가 가장 중요합니다. 그 블록 안에는 "[출처: 문서 제목]" 형태의 표시가 문서마다
    붙어있습니다 — 그 블록의 내용을 근거로 답할 때는 반드시 문장 끝에 그 출처를 그대로
    괄호로 표시하세요. 예: "31개 업체가 입주해 있습니다. (출처: 김해시 고시 제2025-216호)".
    여러 출처의 내용을 같이 썼다면 관련된 출처를 전부 표시하세요. 숫자·업체명·조항 번호는
    문서 원문에 적힌 그대로 옮기고, 어림잡거나 반올림·요약하지 마세요 — "30개사쯤", "대략"처럼
    부정확하게 답하지 말고, 원문 표현이 애매하면("OOO 외 30개사" 등) 그 애매함까지 그대로
    설명하세요 (예: "㈜구보 외 30개사로 표기되어 있어 구보를 포함하면 총 31개사입니다").
    절대로 원문에 없는 예시(업종명, 회사명, 수치 등)를 만들어 덧붙이고 그걸 같은 출처로
    표시하지 마세요 — 예를 들어 원문이 "도금시설은 입주 제한"이라고만 했는데 "타이어
    제조업, 시멘트 제조업" 같은 예시를 스스로 지어내 같은 출처 표시로 답하면 안 됩니다.
    원문에 있는 항목만 그대로 나열하고, 이해를 돕는 일반적 설명을 덧붙이고 싶으면 그
    부분은 출처 표시 없이 "(참고로 일반적으로는 ~)"처럼 원문과 명확히 구분해서 쓰세요.

참고 문서:
{context}
"""

def _load_docs() -> str:
    """subsidy_docs.txt 전체 로드 (없으면 빈 문자열).
    문서 전체를 그대로 프롬프트에 넣는 게 아니라, 질의 시점에
    _keyword_filter_context()가 관련 단락만 골라내 2200자로 줄이므로
    여기서 앞부분만 잘라내면 뒤쪽에 추가된 내용이 검색 자체가 불가능해진다
    (실제로 이 버그 때문에 문서 뒷부분에 추가한 산단 정보를 챗봇이 못 찾고
    환각 답변을 낸 사례가 있었음 — 반드시 전체를 로드해야 함)."""
    if not os.path.exists(DOCS_PATH):
        return ""
    try:
        with open(DOCS_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# 한국어는 명사에 조사가 붙어 띄어쓰기 기준 토큰과 문서 표기가 어긋나기 쉽다
# (예: 질문의 "현덕지구도" ≠ 문서의 "현덕지구"). 흔한 조사를 뒤에서부터 제거해
# 명사 자체를 비교 대상으로 삼는다. 길이 긴 조사부터 검사해야 짧은 조사가 먼저
# 걸려 어중간하게 잘리는 것을 막을 수 있다.
_TRAILING_PARTICLES = sorted(
    ["으로서", "으로써", "이라서", "에서는", "에서도", "이라도",
     "에서", "에게", "한테", "에는", "에도", "까지", "부터", "이나", "라도", "만은",
     "은", "는", "이", "가", "을", "를", "의", "도", "만", "과", "와", "로", "에", "나"],
    key=len, reverse=True,
)


_TRAILING_PUNCT = "?!.,~·…\"'()[]{}:;"
_PARTICLES_SET = set(_TRAILING_PARTICLES)


def _strip_trailing_particle(word: str) -> str:
    """단어 끝의 흔한 조사를 하나 제거한다 (명사가 2자 미만으로 줄어들면 원래 단어 유지)."""
    for p in _TRAILING_PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


# 실제 검색 사고 사례: "혜택은?"처럼 문장부호가 붙은 단어는 조사 제거가 "은?"과
# 안 맞아 실패했고("혜택"이라는 핵심 키워드가 통째로 유실됨), "있는"/"받을"처럼
# 정보량이 거의 없는 기능어는 아무 문단에나 걸려서 엉뚱한 문단(예: 산업집적법 조문)이
# 점수만 높아 1위로 뽑히는 문제가 있었다 — 그 결과 문서에 실제로 있는 "창업 기업
# 산업단지 입주 혜택" 섹션을 놔두고 챗봇이 "해당 정보는 없습니다"라고 답한 사례 발생.
_STOPWORDS = {
    "있는", "있다", "없는", "없다", "받을", "받는", "하는", "하다", "되는", "되다",
    "그리고", "그런데", "그래서", "어떻게", "무엇", "어디", "언제", "누구", "이런",
    "저런", "그런", "때는", "경우",
}


def _keyword_filter_context(docs_text: str, query: str, max_chars: int = 2200) -> str:
    """쿼리 키워드가 포함된 단락을 우선 반환 (간단한 관련성 필터)"""
    if not docs_text:
        return "관련 문서 없음"

    paragraphs = [p.strip() for p in docs_text.split("\n\n") if p.strip()]
    raw_words = [w.strip(_TRAILING_PUNCT) for w in query.split()]
    # "길이 2자 이상"만 키워드로 인정하면 "법"·"세"·"돈"·"땅"처럼 실제 의미 있는 한 글자
    # 한국어 명사가 통째로 걸러진다 — 실제로 "산업단지 법 알아?"가 "법"이 사라진 채
    # "산업단지"만 남아 법률 조문 문단을 못 찾고 "해당 정보는 없습니다"로 답한 사례가 있었음.
    # 1글자 단어는 그 자체가 순수 조사(_PARTICLES_SET)일 때만 제외하고, 나머지는 살린다.
    raw_words = [
        w for w in raw_words
        if w not in _STOPWORDS and (len(w) >= 2 or (len(w) == 1 and w not in _PARTICLES_SET))
    ]
    # 원형 토큰과 조사 제거 토큰을 모두 후보로 사용 (조사 제거판이 원형과 다를 때만 추가)
    query_words = [
        w for w in dict.fromkeys(raw_words + [_strip_trailing_particle(w) for w in raw_words])
        if w not in _STOPWORDS
    ]

    # 키워드 포함 단락 우선 정렬 (단락 내 공백을 제거한 버전에도 대조해
    # "안산사이언스밸리"(질문) vs "안산 사이언스밸리"(문서) 같은 띄어쓰기 차이도 흡수)
    #
    # 제목("[...]")에 걸리면 가중치를 더 준다 — 실제 사고 사례: "산업단지 법 알아?"를 물으면
    # "법"이 본문 어딘가에 우연히 한 번 인용된("...지원법 제45조") 여러 문단이 전부 동점으로
    # 묶여, 정작 제목 자체가 "[산업입지 및 개발에 관한 법률]"인 진짜 법률 문단은 파일 뒤쪽에
    # 있다는 이유만으로 밀려나 컨텍스트 예산 안에 못 들어간 적이 있었다. 제목 매치는 그 문단이
    # "무엇에 관한 문단인지"를 직접 말해주므로 본문 우연한 언급보다 훨씬 신뢰도 높은 신호다.
    # "GW" 같은 영문 단지명을 소문자로 물어보면 대소문자 차이로 매칭이 안 되던 버그가
    # 있었다(_find_mentioned_park에서 실제 확인됨) — 여기도 동일하게 소문자로 비교한다.
    query_words_lower = [w.lower() for w in query_words]
    scored = []
    for para in paragraphs:
        para_lower = para.lower()
        para_nospace = para_lower.replace(" ", "")
        header_end = para.find("]")
        header = para_lower[: header_end + 1] if para.startswith("[") and header_end != -1 else ""
        body_hits = sum(1 for w in query_words_lower if w in para_lower or w.replace(" ", "") in para_nospace)
        header_hits = sum(1 for w in query_words_lower if header and (w in header or w.replace(" ", "") in header.replace(" ", "")))
        scored.append((body_hits + header_hits * 3, para))
    scored.sort(key=lambda x: x[0], reverse=True)

    # 상위 단락들을 합쳐서 반환 (최대 2200자 — 1500자였을 때 신청서류 안내처럼
    # 자체로 1500자를 살짝 넘는 완결된 단락이 통째로 버려지는 사례가 있었음).
    # 1위 단락이 너무 커도 곧장 break하지 않고 다음 후보로 넘어가야, 상위 1개는
    # 못 들어가도 2~3위 단락이라도 채울 수 있다(예전엔 break라 아예 빈 컨텍스트가 됨).
    context = ""
    for _, para in scored:
        if len(context) + len(para) > max_chars:
            continue
        context += para + "\n\n"

    return context.strip() or docs_text[:min(1500, max_chars)]


class RAGService:
    def __init__(self, api_key: str, parks: Optional[List[Dict]] = None):
        self.api_key = api_key
        self.parks = parks or []
        # Groq 클라이언트 (스트리밍용)
        self._groq_client = Groq(api_key=api_key)
        # LangChain Groq LLM
        self.llm = ChatGroq(
            model=CHAT_MODEL,
            groq_api_key=api_key,
            temperature=0.3,
            # 900이었을 때 GW 같은 공식 문서 근거 답변(표+여러 항목)이 중간 단어에서
            # 뚝 끊기는 문제가 실제로 있었다 — 1500으로 올리고, 그만큼 TPM 예산을
            # 맞추려고 _get_context()의 컨텍스트 쪽 예산을 줄였다(아래 chat_stream도 동일).
            max_tokens=1500,
            reasoning_effort="low",  # gpt-oss는 추론 모델 — effort를 낮추지 않으면 토큰 예산을 "생각"에 다 씀
        )
        # 문서 로드 (시작 시 1회)
        self._docs_text = _load_docs()
        print(f"✅ RAG 챗봇 초기화 완료 (문서 {len(self._docs_text)}자 로드, 단지 {len(self.parks)}개 조회 가능)")

    def build_vectorstore(self):
        """호환성 유지용 — 실제로는 아무것도 하지 않음"""
        pass

    def load_vectorstore(self) -> bool:
        """호환성 유지용 — True 반환해 build_vectorstore 호출 방지"""
        return True

    def _find_mentioned_park(self, query: str) -> Optional[Dict]:
        """사용자 질문에 등장한 산업단지를 실제 DB 목록에서 찾는다.
        여러 개가 걸리면(예: "구미"가 여러 구미 단지명에 다 포함) 가장 이름이 긴(구체적인) 걸 고른다.
        "GW"(단지명)를 소문자로 "gw"라고 물어보면 못 찾던 버그가 실제로 있었음 — 영문 단지명이
        꽤 있어서(GW, I-FoodPark 등) 대소문자 구분 없이 비교해야 한다."""
        q = query.replace(" ", "").lower()
        best = None
        for p in self.parks:
            name = (p.get("name") or "").replace(" ", "").lower()
            if len(name) < 2:
                continue
            if name in q or q in name:
                if not best or len(name) > len((best.get("name") or "").replace(" ", "")):
                    best = p
        return best

    @staticmethod
    def _format_park_facts(park: Dict) -> str:
        """단지 실측 데이터를 챗봇 컨텍스트용 텍스트로 정리한다."""
        lines = [f"[{park.get('name')} 실측 데이터 — SiteMatch AI DB 기준]"]
        loc = " ".join(x for x in [park.get("region"), park.get("city")] if x)
        if loc:
            lines.append(f"위치: {loc}")
        if park.get("address"):
            lines.append(f"주소: {park['address']}")
        if park.get("type"):
            lines.append(f"유형: {park['type']}")
        dev_status = park.get("dev_status") or "완료"
        if dev_status != "완료":
            lines.append(f"조성상태: {dev_status} (아직 완공 전인 신설 단지)")
        vac = park.get("vacancy_rate")
        # "공실률"이라는 이름은 "빈 땅/건물이 있다"는 뜻으로 오해되기 쉽지만, 이 수치는
        # 실제로는 등록된 입주기업 중 가동 중인 비율(가동률)이다 — 빈 부지 여부는
        # 아래 "가용면적"이 알려주는 전혀 다른 지표이므로 혼동 없이 가동률로 표기한다.
        lines.append(f"가동률(등록 입주기업 중 실제 가동 중인 비율): {round(100 - vac, 1)}%" if vac is not None else "가동률: 데이터 없음")
        if park.get("available_area"):
            lines.append(f"미분양(신규 분양 가능) 면적: {park['available_area']:,.0f}㎡")
        else:
            lines.append("미분양 면적: 데이터 없음(또는 0)")
        if park.get("total_area"):
            lines.append(f"지정면적: {park['total_area']:,.0f}㎡")
        if park.get("industries"):
            lines.append(f"주요 업종(등록공장 기준): {', '.join(park['industries'])}")
        if park.get("rent_per_sqm"):
            lines.append(f"임대료: {park['rent_per_sqm']:,}원/㎡")
        if park.get("subsidy"):
            lines.append(f"지원금: {park['subsidy']}")
        return "\n".join(lines)

    def _get_context(self, query: str) -> str:
        """쿼리 관련 문서 검색 (키워드 필터링) + 질문에 등장한 특정 단지의 실측 데이터,
        그리고 그 단지의 관리기관 공식 고시문서(있으면)를 함께 제공."""
        park = self._find_mentioned_park(query)

        if park:
            # 특정 단지가 특정된 질문은, 그 단지 자체의 공식 문서가 전국 단위
            # subsidy_docs.txt보다 훨씬 더 관련성이 높다 — 전국 문서 예산을 크게
            # 줄이고 그만큼을 그 단지 문서 쪽으로 몰아준다 (질문 자체가 이미 그
            # 단지로 좁혀졌으니 전국 문서가 필요한 경우는 드물다).
            #
            # 단지 공식 문서는 키워드 매칭을 안 쓰고 그냥 앞에서부터 순서대로
            # 자른다 — 실제로 겪은 문제: "입주업체가 몇 개사야?"라고 물으면
            # 정답이 있는 페이지(표 헤더가 "업체수"/"㈜구보 외 30개사")에는 정작
            # "입주업체"라는 글자가 그대로 없고, 엉뚱한 페이지("입주업체에 대한
            # 지원활동" 같은 문장)에만 그 글자가 우연히 들어있어서 키워드 매칭이
            # 오히려 틀린 페이지를 골랐다. 단지 공식 문서는 하나같이 그 단지
            # 얘기뿐이라 "관련 없는 내용을 걸러낼 필요"가 애초에 거의 없으므로,
            # 이 경우엔 정밀 필터링보다 통째로 넣는 쪽이 더 정확하다.
            # (budget 배분은 get_park_documents_text 안에서 파일별로 균등하게
            # 하므로 여기서 다시 자르지 않는다 — 한 단지에 PDF+txt처럼 문서가
            # 여러 개일 때 앞 파일이 예산을 다 써서 뒤 파일이 통째로 사라지는
            # 문제가 실제로 있었음)
            # 예산 수치는 max_tokens를 900→1500으로 올리면서 같이 줄인 것 —
            # 컨텍스트를 그대로 두고 완성 토큰만 늘리면 Groq 무료 티어 TPM(분당
            # 8000토큰) 한도를 넘겨 요청 자체가 거부되므로, 전체 합이 비슷하게
            # 유지되도록 컨텍스트 쪽에서 줄였다.
            doc_context = _keyword_filter_context(self._docs_text, query, max_chars=500)
            facts = self._format_park_facts(park)
            official = get_park_documents_text(
                park.get("region", ""), park.get("city", ""), park.get("name", ""), budget=6000
            )
            if official:
                facts += f"\n\n[{park.get('name')} 관리기관 공식 고시문서 발췌]\n{official}"
            return facts + "\n\n" + doc_context

        return _keyword_filter_context(self._docs_text, query)

    def chat(self, messages: List[dict]) -> str:
        """동기 챗봇 응답"""
        if not messages:
            return "질문을 입력해주세요."

        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        context = self._get_context(last_user_msg)

        # 대화 히스토리 구성
        history_text = ""
        for msg in messages[:-1]:
            role = "사용자" if msg["role"] == "user" else "AI"
            history_text += f"{role}: {msg['content']}\n"

        prompt = f"""{SYSTEM_PROMPT.format(context=context)}

이전 대화:
{history_text}

사용자: {last_user_msg}
AI:"""

        response = self.llm.invoke(prompt)
        return response.content

    async def chat_stream(self, messages: List[dict]) -> AsyncGenerator[str, None]:
        """스트리밍 챗봇 응답 (Groq 스트리밍)"""
        if not messages:
            yield "질문을 입력해주세요."
            return

        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        context = self._get_context(last_user_msg)

        history_text = ""
        for msg in messages[:-1]:
            role = "사용자" if msg["role"] == "user" else "AI"
            history_text += f"{role}: {msg['content']}\n"

        system_content = SYSTEM_PROMPT.format(context=context)
        user_content = f"이전 대화:\n{history_text}\n사용자: {last_user_msg}"

        # Groq 스트리밍
        stream = self._groq_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=1500,  # 사유는 위 chat()의 동일 옵션 주석 참고
            reasoning_effort="low",
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# 싱글톤 인스턴스
_rag_service: RAGService = None


def get_rag_service() -> RAGService:
    global _rag_service
    return _rag_service


def init_rag_service(api_key: str, parks: Optional[List[Dict]] = None) -> RAGService:
    global _rag_service
    _rag_service = RAGService(api_key, parks)
    # load_vectorstore()가 True를 반환하므로 build_vectorstore()는 호출되지 않음
    if not _rag_service.load_vectorstore():
        _rag_service.build_vectorstore()
    return _rag_service
