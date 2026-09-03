"""
LangChain RAG + Groq 기반 챗봇 서비스
임베딩/FAISS 없이 subsidy_docs.txt를 직접 컨텍스트로 활용 (Render 무료 플랜 최적화)
"""
import os
from typing import List, AsyncGenerator, Dict, Optional

from groq import Groq
from langchain_groq import ChatGroq

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_PATH = os.path.join(BASE_DIR, "data", "subsidy_docs.txt")

# llama-3.3-70b-versatile는 Groq에서 서비스 종료되어 대체 모델로 전환 (2026-08 확인)
CHAT_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """당신은 한국 산업단지 입주 전문 상담 AI 'SiteMatch AI'입니다.
산업단지 입주와 관련된 지원금, 인허가 절차, 세금 혜택, 입지 추천 등에 대해 
친절하고 구체적으로 답변하세요.

다음 규칙을 따르세요:
1. 실제 제도명과 금액을 최대한 포함하세요
2. 답변은 400자 이내로 간결하게 작성하세요
3. 한국어로만 답변하세요
4. 모르는 내용은 "한국산업단지공단(www.kicox.or.kr)에 문의하세요"라고 안내하세요
5. 아래 참고 문서를 활용하여 정확한 정보를 제공하세요
6. 참고 문서 맨 앞에 "[단지명 실측 데이터]" 블록이 있다면, 그건 SiteMatch DB에서 방금 조회한
   실제 수치입니다 — 질문한 단지에 대한 답변은 반드시 이 블록의 수치를 그대로 인용하세요.
   이 블록이 없다면, 또는 블록은 있어도 물어본 항목(예: 공실률)이 그 안에 안 적혀 있다면,
   절대로 다른 수치(면적 등)로부터 계산·추정해서 만들어내지 말고 "해당 정보는 없습니다"라고
   솔직히 답하세요. 없는 수치를 그럴듯하게 계산해서 답하는 것이 가장 나쁜 답변입니다.

참고 문서:
{context}
"""

def _load_docs() -> str:
    """subsidy_docs.txt 전체 로드 (없으면 빈 문자열).
    문서 전체를 그대로 프롬프트에 넣는 게 아니라, 질의 시점에
    _keyword_filter_context()가 관련 단락만 골라내 1500자로 줄이므로
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


def _strip_trailing_particle(word: str) -> str:
    """단어 끝의 흔한 조사를 하나 제거한다 (명사가 2자 미만으로 줄어들면 원래 단어 유지)."""
    for p in _TRAILING_PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def _keyword_filter_context(docs_text: str, query: str) -> str:
    """쿼리 키워드가 포함된 단락을 우선 반환 (간단한 관련성 필터)"""
    if not docs_text:
        return "관련 문서 없음"

    paragraphs = [p.strip() for p in docs_text.split("\n\n") if p.strip()]
    raw_words = [w for w in query.split() if len(w) >= 2]
    # 원형 토큰과 조사 제거 토큰을 모두 후보로 사용 (조사 제거판이 원형과 다를 때만 추가)
    query_words = list(dict.fromkeys(
        raw_words + [_strip_trailing_particle(w) for w in raw_words]
    ))

    # 키워드 포함 단락 우선 정렬 (단락 내 공백을 제거한 버전에도 대조해
    # "안산사이언스밸리"(질문) vs "안산 사이언스밸리"(문서) 같은 띄어쓰기 차이도 흡수)
    scored = []
    for para in paragraphs:
        para_nospace = para.replace(" ", "")
        hits = sum(1 for w in query_words if w in para or w.replace(" ", "") in para_nospace)
        scored.append((hits, para))
    scored.sort(key=lambda x: x[0], reverse=True)

    # 상위 단락들을 합쳐서 반환 (최대 1500자)
    context = ""
    for _, para in scored:
        if len(context) + len(para) > 1500:
            break
        context += para + "\n\n"

    return context.strip() or docs_text[:1500]


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
            max_tokens=600,
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
        여러 개가 걸리면(예: "구미"가 여러 구미 단지명에 다 포함) 가장 이름이 긴(구체적인) 걸 고른다."""
        q = query.replace(" ", "")
        best = None
        for p in self.parks:
            name = (p.get("name") or "").replace(" ", "")
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
        if park.get("type"):
            lines.append(f"유형: {park['type']}")
        vac = park.get("vacancy_rate")
        lines.append(f"공실률(입주 대비 미가동 비율): {vac}%" if vac is not None else "공실률: 데이터 없음")
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
        """쿼리 관련 문서 검색 (키워드 필터링) + 질문에 등장한 특정 단지의 실측 데이터를 함께 제공."""
        doc_context = _keyword_filter_context(self._docs_text, query)
        park = self._find_mentioned_park(query)
        if park:
            return self._format_park_facts(park) + "\n\n" + doc_context
        return doc_context

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
            max_tokens=600,
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
