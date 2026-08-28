import logging

import uvicorn
from fastapi import FastAPI

from .config import PORT
from .webhook import router as webhook_router
from .llm_client import get_llm_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Code Review Agent")
app.include_router(webhook_router)


@app.get("/")
async def root():
    # Health-check: confirme que le serveur est vivant
    return {"status": "running", "version": "1.0.1"}


@app.get("/stats")
async def stats():
    # Observabilité US 4.2: statistiques d'utilisation LLM (calls, tokens, rejets)
    return get_llm_stats()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
