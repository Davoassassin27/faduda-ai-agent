"""
main.py - API FastAPI para el chatbot de metricas.

Endpoints:
  GET  /         -> sirve static/index.html (chat UI)
  GET  /health   -> estado de MySQL y Gemini
  POST /chat     -> {question} -> respuesta del agente (LLM + tools)
  POST /predict  -> {metric, periods} -> forecast directo (sin LLM)
  GET  /schema   -> esquema de la BD (para transparencia / debug)

Seguridad:
  * CORS restringido por ALLOWED_ORIGIN (env). '*' solo para desarrollo.
  * Tipado Pydantic en requests.
  * Las excepciones se capturan y se devuelven en espanol, sin stack traces.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import llm
import predictor

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("faduda.main")

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

app = FastAPI(
    title="FADUA - Chatbot de Metricas de Campanas",
    description="Agente de IA con function calling: conversa en lenguaje natural "
    "sobre KPIs de campañas (Google/Meta Ads, leads, ventas) y predice el proximo mes.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGIN.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Modelos de request
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000,
                          description="Pregunta en lenguaje natural sobre metricas.")


class PredictRequest(BaseModel):
    metric: str = Field(..., description="ventas | leads | ingresos | leads_google | leads_meta")
    periods: int = Field(1, ge=1, le=12, description="Meses futuros a proyectar.")


class ChatResponse(BaseModel):
    answer: str
    sql: list[str] = []
    db_results: list[dict] = []
    forecasts: list[dict] = []
    tool_calls: list[dict] = []
    finish_reason: str | None = None
    truncated: bool = False
    elapsed_s: float | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    db_ok, db_msg = db.test_connection()
    llm_ok, llm_msg = llm.test_gemini()
    return {
        "status": "ok" if (db_ok and llm_ok) else "degraded",
        "database": {"ok": db_ok, "detail": db_msg},
        "llm": {"ok": llm_ok, "detail": llm_msg, "model": llm.GEMINI_MODEL},
    }


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        result = llm.chat(req.question)
        return result
    except RuntimeError as e:
        # Cuota agotada / IA no respondio
        logger.warning("chat fallo: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("error inesperado en /chat")
        raise HTTPException(status_code=500, detail=f"Error interno: {e}")


@app.get("/metrics")
def metrics():
    """Info util para la entrevista: estado de caches y rate hints."""
    import predictor
    return {
        "forecast_cache_entries": len(predictor._FC_CACHE),
        "schema_cache_age_s": round(time.time() - db._SCHEMA_CACHE.get("ts", 0), 1),
    }


@app.post("/predict")
def predict(req: PredictRequest):
    try:
        return predictor.forecast_metric(req.metric, req.periods)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("error inesperado en /predict")
        raise HTTPException(status_code=500, detail=f"Error interno: {e}")


@app.get("/schema")
def schema():
    """Devuelve el esquema descubierto dinamicamente (transparencia tecnica)."""
    try:
        return {"schema": db.get_schema_description()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo esquema: {e}")


# ---------------------------------------------------------------------------
# Frontend statico
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"message": "Chatbot API arriba. Frontend no encontrado."}, status_code=200)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)