"""
SQLite 데이터베이스 모델 및 초기화
"""
import json
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sitematch.db")
DATA_PATH = os.path.join(BASE_DIR, "data", "industrial_parks.json")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class IndustrialPark(Base):
    __tablename__ = "industrial_parks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    region = Column(String(50))
    city = Column(String(100))
    type = Column(String(50))
    total_area = Column(Float)
    available_area = Column(Float)
    vacancy_rate = Column(Float)
    rent_per_sqm = Column(Integer)
    industries = Column(Text)       # JSON 문자열
    logistics = Column(Text)        # JSON 문자열
    features = Column(Text)         # JSON 문자열
    monthly_inquiries = Column(Integer, default=0)
    description = Column(Text)
    subsidy = Column(Text)
    contact = Column(String(50))
    website = Column(String(200))
    lat = Column(Float)
    lng = Column(Float)
    updated_at = Column(DateTime, default=datetime.utcnow)


class MatchingHistory(Base):
    __tablename__ = "matching_history"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(100))
    industry = Column(String(50))
    size = Column(String(50))
    area = Column(String(50))
    region = Column(String(50))
    budget = Column(String(50))
    logistics = Column(String(100))
    extra = Column(Text)
    matched_parks = Column(Text)    # JSON 문자열 (추천 결과)
    status = Column(String(30), default="매칭 완료")
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100))
    role = Column(String(10))       # user / assistant
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """DB 테이블 생성 및 초기 데이터 로드"""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # 이미 데이터가 있으면 스킵
        if db.query(IndustrialPark).count() > 0:
            return
        # JSON 파일에서 초기 데이터 로드
        if os.path.exists(DATA_PATH):
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                parks = json.load(f)
            for p in parks:
                park = IndustrialPark(
                    id=p["id"],
                    name=p["name"],
                    region=p["region"],
                    city=p["city"],
                    type=p["type"],
                    total_area=p["total_area"],
                    available_area=p["available_area"],
                    vacancy_rate=p["vacancy_rate"],
                    rent_per_sqm=p["rent_per_sqm"],
                    industries=json.dumps(p["industries"], ensure_ascii=False),
                    logistics=json.dumps(p["logistics"], ensure_ascii=False),
                    features=json.dumps(p["features"], ensure_ascii=False),
                    monthly_inquiries=p["monthly_inquiries"],
                    description=p["description"],
                    subsidy=p["subsidy"],
                    contact=p["contact"],
                    website=p["website"],
                    lat=p["lat"],
                    lng=p["lng"],
                )
                db.add(park)
            db.commit()
            print(f"✅ {len(parks)}개 산업단지 데이터 로드 완료")
    finally:
        db.close()
