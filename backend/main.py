"""
SiteMatch AI - FastAPI 메인 앱
산업단지 공실 AI 매칭 플랫폼 백엔드
"""
import os
import sys
import json
import asyncio
from contextlib import asynccontextmanager

# Windows 콘솔(cp949 등)에서 이모지·한글 print가 UnicodeEncodeError로 죽는 것을 방지
# (uvicorn --reload가 띄우는 자식 프로세스는 PYTHONIOENCODING 환경변수를 물려받지 못할 수 있음)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
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
            init_rag_service(GROQ_API_KEY, parks_data)
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
        # 업종 정보 백필 (일일 트래픽 1,000회 제한 — 매일 새벽 3시에 이어서 처리)
        scheduler.add_job(
            industry_backfill_job,
            "cron",
            hour=3,
            minute=0,
            id="industry_backfill",
        )
        scheduler.start()
        print("✅ ETL 스케줄러 시작 (매일 02:00 공공데이터 갱신 / 03:00 업종 백필)")
    else:
        print("⚠️ GROQ_API_KEY 미설정 → AI 기능 비활성화")
        print("   backend/.env 파일에 GROQ_API_KEY를 설정하세요")

    _port = os.environ.get("PORT", "8000")
    print("🎉 SiteMatch AI 백엔드 준비 완료!")
    print(f"   API 문서: http://localhost:{_port}/docs")
    print(f"   프론트엔드: http://localhost:{_port}")

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


async def industry_backfill_job():
    """등록공장 생산정보 API로 단지별 업종 정보를 채우는 작업.
    일일 트래픽 1,000회 제한 때문에 하루치만 처리하고, 남은 단지는 다음날 이어서 처리한다."""
    if not PUBLIC_DATA_API_KEY:
        print("⚠️ 업종 백필 건너뜀: PUBLIC_DATA_API_KEY 미설정")
        return
    print(f"🔄 업종 백필 작업 시작: {__import__('datetime').datetime.now()}")
    from services.industry_backfill import run_backfill
    try:
        result = run_backfill(PUBLIC_DATA_API_KEY)
        if result["remaining"] == 0:
            print("🎉 전국 단지 업종 백필 완전히 완료됨")
    except Exception as e:
        print(f"⚠️ 업종 백필 오류: {e}")


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

# PWA 아이콘·manifest.json — "홈 화면에 추가"로 설치했을 때 주소창/네비바 없이
# 전체화면 앱처럼 열리게 하는 데 필요 (일반 브라우저 탭으로 볼 때는 이 설정과
# 무관하게 항상 브라우저 UI가 보임 — 웹페이지가 그걸 없앨 방법은 없음)
_static_dir = os.path.join(_backend_dir, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def serve_frontend():
    """프론트엔드 HTML 서빙. 카카오맵 키는 /kakao-sdk.js 프록시 라우트에서
    서버 쪽에서만 사용하므로 HTML 자체에는 더 이상 주입할 게 없다."""
    if os.path.exists(FRONTEND_PATH):
        return FileResponse(FRONTEND_PATH)
    return {"message": "SiteMatch AI API", "docs": "/docs"}


@app.get("/manifest.json")
async def serve_manifest():
    """PWA manifest — 관례상 루트 경로로도 접근 가능하게 함"""
    return FileResponse(os.path.join(_static_dir, "manifest.json"), media_type="application/manifest+json")


_kakao_sdk_cache = {"body": None, "content_type": "text/javascript"}


@app.get("/kakao-sdk.js")
async def proxy_kakao_sdk():
    """카카오맵 SDK를 dapi.kakao.com에서 직접 브라우저가 받지 않고, 우리 서버가
    대신 받아와서 같은 출처(same-origin)로 내려준다.

    실제로 겪은 문제: 크롬의 ORB(Opaque Response Blocking) 보안 기능이 이 스크립트를
    크로스오리진 <script> 태그로 불러올 때 net::ERR_BLOCKED_BY_ORB로 막는 사례가
    PC/모바일 가리지 않고 실제로 발생했다 (배포 사이트에서도 재현됨). 광고 차단
    확장기능이 "kakao.com" 도메인 패턴을 걸러내는 경우도 있어, 두 문제 다 같은
    출처로 우회하면 근본적으로 피해간다. 매 요청마다 다시 받아오지 않도록
    프로세스 메모리에 캐시한다(이 파일은 자주 안 바뀜, 서버 재시작하면 다시 받아옴)."""
    if _kakao_sdk_cache["body"] is None:
        import httpx
        key = os.environ.get("KAKAO_MAP_KEY", "")
        url = f"https://dapi.kakao.com/v2/maps/sdk.js?appkey={key}&autoload=false"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
        resp.raise_for_status()
        _kakao_sdk_cache["body"] = resp.content
        _kakao_sdk_cache["content_type"] = resp.headers.get("content-type", "text/javascript")
    return Response(content=_kakao_sdk_cache["body"], media_type=_kakao_sdk_cache["content_type"])


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
