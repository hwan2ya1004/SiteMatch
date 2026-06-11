"""
SiteMatch AI - FastAPI 메인 앱
산업단지 공실 AI 매칭 플랫폼 백엔드
"""
import os
import sys
import json
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 현재 디렉토리를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PUBLIC_DATA_API_KEY = os.getenv("PUBLIC_DATA_API_KEY", "")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행"""
    print("🚀 SiteMatch AI 백엔드 시작 중...")

    # 1. DB 초기화
    from database import init_db, SessionLocal
    init_db()
    print("✅ DB 초기화 완료")

    # 2. AI 서비스 초기화 (Groq API 키 기반)
    if GROQ_API_KEY:
        # DB에서 공단 데이터 로드
        db = SessionLocal()
        try:
            from database import IndustrialPark
            parks = db.query(IndustrialPark).all()
            parks_data = []
            for p in parks:
                d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
                for field in ["industries", "logistics", "features"]:
                    if isinstance(d.get(field), str):
                        try:
                            d[field] = json.loads(d[field])
                        except Exception:
                            d[field] = []
                parks_data.append(d)
        finally:
            db.close()

        # 임베딩 서비스 초기화 (키워드 방식 — 즉시 완료)
        try:
            from services.embedding import init_embedding_service
            init_embedding_service(GROQ_API_KEY, parks_data)
            print("✅ AI 매칭 엔진 초기화 완료")
        except Exception as e:
            print(f"⚠️ AI 매칭 엔진 초기화 실패: {e}")

        # RAG 서비스 초기화 (Groq 직접 호출 — 즉시 완료)
        try:
            from services.rag import init_rag_service
            init_rag_service(GROQ_API_KEY)
            print("✅ RAG 챗봇 초기화 완료")
        except Exception as e:
            print(f"⚠️ RAG 챗봇 초기화 실패: {e}")

        # 스케줄러 시작 (일 1회 새벽 2시 공공데이터 갱신)
        scheduler.add_job(
            daily_etl_job,
            "cron",
            hour=2,
            minute=0,
            id="daily_etl",
        )
        scheduler.start()
        print("✅ ETL 스케줄러 시작 (매일 02:00 갱신)")
    else:
        print("⚠️ GROQ_API_KEY 미설정 → AI 기능 비활성화")
        print("   backend/.env 파일에 GROQ_API_KEY를 설정하세요")

    print("🎉 SiteMatch AI 백엔드 준비 완료!")
    print(f"   API 문서: http://localhost:8000/docs")
    print(f"   프론트엔드: http://localhost:8000")

    yield

    # 종료 시 스케줄러 정지
    if scheduler.running:
        scheduler.shutdown()
    print("👋 SiteMatch AI 백엔드 종료")


async def daily_etl_job():
    """일 1회 공공데이터 갱신 작업"""
    print(f"🔄 ETL 작업 시작: {__import__('datetime').datetime.now()}")
    from database import SessionLocal
    from services.public_data import PublicDataService

    db = SessionLocal()
    try:
        svc = PublicDataService(api_key=PUBLIC_DATA_API_KEY or None)
        updated = svc.sync_to_db(db)
        print(f"✅ ETL 완료: {updated}개 공단 업데이트")
    except Exception as e:
        print(f"⚠️ ETL 오류: {e}")
    finally:
        db.close()


# FastAPI 앱 생성
app = FastAPI(
    title="SiteMatch AI API",
    description="산업단지 공실 AI 매칭 플랫폼 백엔드 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 설정 (프론트엔드 연동)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
from routers.match import router as match_router
from routers.chat import router as chat_router
from routers.dashboard import router as dashboard_router

app.include_router(match_router)
app.include_router(chat_router)
app.include_router(dashboard_router)

# 정적 파일 서빙 (프론트엔드 HTML)
# backend/ 폴더 내부 경로 우선, 없으면 상위 폴더에서 찾음
_backend_dir = os.path.dirname(os.path.abspath(__file__))
FRONTEND_PATH = os.path.join(_backend_dir, "SiteMatchAI.html")
if not os.path.exists(FRONTEND_PATH):
    FRONTEND_PATH = os.path.join(os.path.dirname(_backend_dir), "SiteMatchAI.html")


@app.get("/")
async def serve_frontend():
    """프론트엔드 HTML 서빙"""
    if os.path.exists(FRONTEND_PATH):
        return FileResponse(FRONTEND_PATH)
    return {"message": "SiteMatch AI API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    """헬스 체크"""
    from services.embedding import get_embedding_service
    from services.rag import get_rag_service

    return {
        "status": "ok",
        "ai_matching": get_embedding_service() is not None,
        "rag_chatbot": get_rag_service() is not None,
        "groq_key_set": bool(GROQ_API_KEY),
    }


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
