import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 환경변수로 접속 정보 관리 (.env 사용 권장)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mes_user:mes_password@localhost:5432/mini_mes",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
