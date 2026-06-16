from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import agent

app = FastAPI(title="Agent Gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent.router, prefix="/api", tags=["agent"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "agent-gateway"}
