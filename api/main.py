"""TenderHawk API. Run: uvicorn api.main:app --reload"""
import redis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.config import REDIS_URL, fallback_mode
from api.db import engine, get_db
from api.models import Profile
from api.schemas import Created, ProfileIn, ProfileOut

app = FastAPI(title="TenderHawk")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    status = {"db": False, "redis": False, "fallback_mode": fallback_mode()}
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        status["db"] = True
    except Exception:
        pass
    try:
        status["redis"] = bool(redis.Redis.from_url(REDIS_URL, socket_timeout=1).ping())
    except Exception:
        pass
    return status


@app.post("/profiles", response_model=Created, status_code=201)
def create_profile(body: ProfileIn, db: Session = Depends(get_db)):
    p = Profile(**body.model_dump())
    db.add(p)
    db.commit()
    return Created(id=p.id)


@app.get("/profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    p = db.get(Profile, profile_id)
    if not p:
        raise HTTPException(404, "profile not found")
    return p
