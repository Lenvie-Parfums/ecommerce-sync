"""
main.py — FastAPI ecommerce-sync
Endpoints:
  GET  /health  → liveness check
  GET  /status  → progresso atual
  POST /sync    → dispara sincronização (protegido por token)
  GET  /sync    → idem (pra chamar pelo browser/menu Sheets)
"""
import os, logging, threading
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import sync as sync_engine

app = FastAPI(title="Ecommerce Sync — Lenvie", version="1.0.0")

SYNC_TOKEN = os.getenv("SYNC_TOKEN", "")  # token de proteção do /sync


def _verificar_token(request: Request):
    if not SYNC_TOKEN:
        return  # sem token configurado, aceita qualquer requisição
    token = request.headers.get("x-sync-token") or request.query_params.get("token")
    if token != SYNC_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


@app.get("/health")
def health():
    return {"ok": True, "service": "ecommerce-sync"}


@app.get("/status")
def status():
    s = sync_engine.state
    return {
        "rodando": s.rodando,
        "etapa":   s.etapa,
        "total":   s.total,
        "atual":   s.atual,
        "pct":     round((s.atual / s.total * 100), 1) if s.total else 0,
        "erros":   s.erros,
        "inicio":  s.inicio,
        "fim":     s.fim,
    }


@app.get("/sync")
@app.post("/sync")
def disparar_sync(request: Request):
    _verificar_token(request)

    if sync_engine.state.rodando:
        return JSONResponse({"ok": False, "msg": "Já está rodando.", "status": status()})

    # Roda em thread separada pra não travar o endpoint
    def _rodar():
        result = sync_engine.rodar_sync()
        log.info(f"[Sync] Resultado: {result}")

    t = threading.Thread(target=_rodar, daemon=True)
    t.start()

    return {
        "ok":  True,
        "msg": "Sincronização iniciada em background.",
        "acompanhe": "/status"
    }
