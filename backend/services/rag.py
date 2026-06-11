"""
LangChain RAG + Groq (Llama 3.3 70B) 기반 챗봇 서비스
무료 API: https://console.groq.com
임베딩: HuggingFace sentence-transformers (로컬, 무료)
"""
import os
from typing import List, AsyncGenerator

from groq import Groq
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS as LangchainFAISS
from langchain_core.documents import Document

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_PATH = os.path.join(BASE_DIR, "data", "subsidy_docs.txt")
RAG_INDEX_PATH = os.path.join(BASE_DIR, "data", "rag_faiss_index")

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
        # HuggingFace 로컬 임베딩 (무료, 인터넷 불필요)
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        self.vectorstore = None

    def build_vectorstore(self):
        """지원금 문서로 RAG 벡터스토어 구축"""
        print("🔄 RAG 벡터스토어 구축 중...")
        if not os.path.exists(DOCS_PATH):
            print("⚠️ 지원금 문서 파일이 없습니다.")
            return

        with open(DOCS_PATH, "r", encoding="utf-8") as f:
            text = f.read()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", ".", " "],
        )
        chunks = splitter.split_text(text)
        docs = [Document(page_content=chunk) for chunk in chunks]

        self.vectorstore = LangchainFAISS.from_documents(docs, self.embeddings)
        self.vectorstore.save_local(RAG_INDEX_PATH)
        print(f"✅ RAG 벡터스토어 구축 완료 ({len(docs)}개 청크)")

    def load_vectorstore(self) -> bool:
        """저장된 RAG 벡터스토어 로드"""
        if not os.path.exists(RAG_INDEX_PATH):
            return False
        try:
            self.vectorstore = LangchainFAISS.load_local(
                RAG_INDEX_PATH,
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
            print(f"✅ RAG 벡터스토어 로드 완료")
            return True
        except Exception as e:
            print(f"⚠️ RAG 벡터스토어 로드 실패 (재구축): {e}")
            return False

    def _get_context(self, query: str) -> str:
        """쿼리 관련 문서 검색"""
        if self.vectorstore is None:
            return "관련 문서를 찾을 수 없습니다."
        docs = self.vectorstore.similarity_search(query, k=3)
        return "\n\n".join([doc.page_content for doc in docs])

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
    if not _rag_service.load_vectorstore():
        _rag_service.build_vectorstore()
    return _rag_service
