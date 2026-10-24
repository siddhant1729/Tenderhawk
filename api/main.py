"""TenderHawk API. Run: uvicorn api.main:app --reload"""
import uuid

import redis
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.config import REDIS_URL, fallback_mode
from api import events
from api.db import engine, get_db
from api.models import Profile
from api.schemas import Created, ProfileIn, ProfileOut, SweepIn, SweepOut
from api.sweeps import run_sweep

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


@app.post("/sweeps", response_model=SweepOut, status_code=202)
def start_sweep(body: SweepIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.get(Profile, body.profile_id):
        raise HTTPException(404, "profile not found")
    sweep_id = str(uuid.uuid4())
    events.register_sweep(sweep_id, body.profile_id)
    background.add_task(run_sweep, sweep_id, body.profile_id)
    return SweepOut(sweep_id=sweep_id)


@app.get("/sweeps/{sweep_id}/events")
def sweep_events(sweep_id: str):
    if not events.sweep_exists(sweep_id):
        raise HTTPException(404, "sweep not found")
    return StreamingResponse(events.stream(sweep_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
