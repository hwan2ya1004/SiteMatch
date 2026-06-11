"""
LangChain RAG + Groq (Llama 3.3 70B) 기반 챗봇 서비스
임베딩/FAISS 없이 subsidy_docs.txt를 직접 컨텍스트로 활용 (Render 무료 플랜 최적화)
"""
import os
from typing import List, AsyncGenerator

from groq import Groq
from langchain_groq import ChatGroq

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_PATH = os.path.join(BASE_DIR, "data", "subsidy_docs.txt")

SYSTEM_PROMPT = """당신은 한국 산업단지 입주 전문 상담 AI 'SiteMatch AI'입니다.
산업단지 입주와 관련된 지원금, 인허가 절차, 세금 혜택, 입지 추천 등에 대해 
친절하고 구체적으로 답변하세요.

다음 규칙을 따르세요:
1. 실제 제도명과 금액을 최대한 포함하세요
2. 답변은 400자 이내로 간결하게 작성하세요
3. 한국어로만 답변하세요
4. 모르는 내용은 "한국산업단지공단(www.kicox.or.kr)에 문의하세요"라고 안내하세요
5. 아래 참고 문서를 활용하여 정확한 정보를 제공하세요

참고 문서:
{context}
"""

# 문서 최대 길이 (토큰 절약을 위해 앞부분 4000자만 사용)
MAX_CONTEXT_CHARS = 4000


def _load_docs() -> str:
    """subsidy_docs.txt 로드 (없으면 빈 문자열)"""
    if not os.path.exists(DOCS_PATH):
        return ""
    try:
        with open(DOCS_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        return text[:MAX_CONTEXT_CHARS]
    except Exception:
        return ""


def _keyword_filter_context(docs_text: str, query: str) -> str:
    """쿼리 키워드가 포함된 단락을 우선 반환 (간단한 관련성 필터)"""
    if not docs_text:
        return "관련 문서 없음"

    paragraphs = [p.strip() for p in docs_text.split("\n\n") if p.strip()]
    query_words = [w for w in query.split() if len(w) >= 2]

    # 키워드 포함 단락 우선 정렬
    scored = []
    for para in paragraphs:
        hits = sum(1 for w in query_words if w in para)
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
    def __init__(self, api_key: str):
        self.api_key = api_key
        # Groq 클라이언트 (스트리밍용)
        self._groq_client = Groq(api_key=api_key)
        # LangChain Groq LLM
        self.llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            groq_api_key=api_key,
            temperature=0.3,
            max_tokens=600,
        )
        # 문서 로드 (시작 시 1회)
        self._docs_text = _load_docs()
        print(f"✅ RAG 챗봇 초기화 완료 (문서 {len(self._docs_text)}자 로드)")

    def build_vectorstore(self):
        """호환성 유지용 — 실제로는 아무것도 하지 않음"""
        pass

    def load_vectorstore(self) -> bool:
        """호환성 유지용 — True 반환해 build_vectorstore 호출 방지"""
        return True

    def _get_context(self, query: str) -> str:
        """쿼리 관련 문서 검색 (키워드 필터링)"""
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
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=600,
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


def init_rag_service(api_key: str) -> RAGService:
    global _rag_service
    _rag_service = RAGService(api_key)
    # load_vectorstore()가 True를 반환하므로 build_vectorstore()는 호출되지 않음
    if not _rag_service.load_vectorstore():
        _rag_service.build_vectorstore()
    return _rag_service
