# SiteMatch AI — 산업단지 공실 AI 매칭 플랫폼

> 전국 산업단지 공실 2,200만㎡ 문제를 AI로 해결하는 B2G SaaS 플랫폼

---

## 🚀 빠른 시작

### 1. Gemini API 키 발급 (무료)
1. [Google AI Studio](https://aistudio.google.com) 접속
2. **Get API Key** → API 키 복사

### 2. API 키 설정
```
backend/.env 파일 열기 → GEMINI_API_KEY=발급받은_키_입력
```

### 3. 서버 실행
```powershell
cd c:\SiteMatch\backend
python main.py
```

### 4. 브라우저 접속
- **플랫폼**: http://localhost:8000
- **API 문서**: http://localhost:8000/docs

---

## 📁 프로젝트 구조

```
SiteMatch/
├── SiteMatchAI.html              # 프론트엔드 (단일 HTML)
└── backend/
    ├── main.py                   # FastAPI 메인 앱 + 스케줄러
    ├── database.py               # SQLite DB 모델 + 초기 데이터
    ├── requirements.txt          # Python 패키지 목록
    ├── .env                      # API 키 설정 (직접 수정 필요)
    ├── .env.example              # 환경변수 예시
    ├── data/
    │   ├── industrial_parks.json # 12개 산업단지 내장 데이터
    │   └── subsidy_docs.txt      # 지원금·인허가 RAG 문서
    ├── services/
    │   ├── embedding.py          # Gemini 임베딩 + FAISS 매칭 엔진
    │   ├── rag.py                # LangChain RAG + 챗봇 서비스
    │   └── public_data.py        # 공공데이터 ETL 파이프라인
    └── routers/
        ├── match.py              # POST /api/match
        ├── chat.py               # POST /api/chat, WS /ws/chat
        └── dashboard.py          # GET /api/dashboard/*
```

---

## 🧩 구현 모듈

| 모듈 | 기술 스택 | 엔드포인트 |
|------|-----------|-----------|
| **기업 온보딩** | HTML 폼 (업종·규모·면적·지역·물류) | 프론트엔드 |
| **AI 매칭 엔진** | Gemini text-embedding-004 + FAISS 코사인 유사도 | `POST /api/match` |
| **LLM 챗봇** | LangChain RAG + Gemini 2.0 Flash + WebSocket 스트리밍 | `WS /ws/chat` |
| **공실 DB** | 한국산업단지공단 API → SQLite → 일 1회 갱신 | ETL 스케줄러 |
| **관리자 대시보드** | 공단별 문의 현황·매칭 통계·공실 추이 | `GET /api/dashboard/*` |

---

## 🌐 공공데이터 활용

| 제공 기관 | 데이터셋 | 활용 목적 |
|-----------|---------|---------|
| **한국산업단지공단 ★** | 산업단지 입주·공실 현황, 분양가 | 핵심 공실 DB 구성 및 실시간 갱신 |
| 산업통상부 | 지역별 기업 업종·규모 현황 | 기업 수요 프로파일 생성 |
| 대한무역투자진흥공사 | 산업별 투자·수출 동향 | 성장 업종 우선 매칭 가중치 |
| 지자체 공공데이터 | 지역 지원금·인허가 정보 | 입주 인센티브 자동 안내 |

### 공공데이터 API 연동 방법
1. [공공데이터포털](https://www.data.go.kr) 회원가입
2. **한국산업단지공단_산업단지 현황** API 신청
3. `backend/.env`에 `PUBLIC_DATA_API_KEY=발급받은_키` 입력
4. 서버 재시작 시 자동 동기화 (매일 새벽 2시 갱신)

---

## 🔌 API 엔드포인트

### AI 매칭
```http
POST /api/match
Content-Type: application/json

{
  "industry": "전자·반도체",
  "size": "50~200명",
  "area": "2,000~5,000㎡",
  "region": "경기도",
  "budget": "1,000~3,000만원",
  "logistics": "고속도로 IC 15분 이내",
  "extra": "클린룸 설비 필요"
}
```

### 챗봇 (HTTP)
```http
POST /api/chat
Content-Type: application/json

{
  "messages": [
    {"role": "user", "content": "경기도 지원금 조건이 어떻게 되나요?"}
  ]
}
```

### 챗봇 (WebSocket 스트리밍)
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/chat');
ws.send(JSON.stringify({
  messages: [{ role: "user", content: "질문 내용" }]
}));
```

### 대시보드
```http
GET /api/dashboard/stats       # 핵심 통계
GET /api/dashboard/parks       # 공실 현황 목록
GET /api/dashboard/recent-matches  # 최근 매칭 이력
GET /api/parks?region=경기도   # 산업단지 목록 (필터)
```

---

## ⚙️ 패키지 설치

```powershell
pip install -r backend/requirements.txt
```

주요 패키지:
- `fastapi` + `uvicorn` — 웹 서버
- `google-genai` — Gemini 임베딩 + 생성 AI
- `langchain-google-genai` — LangChain Gemini 연동
- `faiss-cpu` — 벡터 유사도 검색
- `sqlalchemy` — SQLite ORM
- `apscheduler` — 일 1회 ETL 스케줄러

---

## 🔑 API 키 없이 실행 (데모 모드)

Gemini API 키 없이도 서버가 실행됩니다:
- ✅ 대시보드: 내장 12개 산업단지 데이터 표시
- ✅ 프론트엔드: 정적 데이터로 동작
- ❌ AI 매칭: API 키 필요
- ❌ 챗봇: API 키 필요

---

## 📊 시스템 아키텍처

```
[브라우저 SiteMatchAI.html]
        │
        ├── POST /api/match ──→ [EmbeddingService]
        │                           └── Gemini text-embedding-004
        │                           └── FAISS 코사인 유사도
        │                           └── SQLite IndustrialPark DB
        │
        ├── WS /ws/chat ────→ [RAGService]
        │                           └── LangChain RAG
        │                           └── FAISS 벡터스토어 (subsidy_docs)
        │                           └── Gemini 2.0 Flash 스트리밍
        │
        └── GET /api/dashboard/* → [DashboardRouter]
                                        └── SQLite 집계 쿼리
                                        └── 공공데이터 ETL (일 1회)
```
