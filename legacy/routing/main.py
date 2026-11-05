import logging
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from webhook import router as webhook_router
import routing.config as config

from backend.core.logging import setup_logging

setup_logging()

app = FastAPI(title="Chiki Routing Module", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router, prefix="/webhook", tags=["webhook"])


@app.get("/")
def root():
    return {"status": "Routing module running"}


@app.get("/chat-history")
def get_chat_history():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM chat_history ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    pass
