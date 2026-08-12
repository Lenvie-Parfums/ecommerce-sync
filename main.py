"""
main.py — FastAPI ecommerce-sync
GET  /health        → liveness
GET  /status        → progresso atual
GET  /sync          → dispara sync (retoma se interrompido)
POST /sync          → idem
GET  /sync?force=true → força reinício do zero
GET  /fix-omie      → corrige linhas da Base Omie com data/NF vazios
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

SYNC_TOKEN = os.getenv("SYNC_TOKEN", "")


def _verificar_token(request: Request):
    if not SYNC_TOKEN:
        return
    token = request.headers.get("x-sync-token") or request.query_params.get("token")
    if token != SYNC_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


@app.get("/health")
def health():
    return {"ok": True, "service": "ecommerce-sync"}


@app.get("/status")
def status():
    return sync_engine.state.to_dict()


@app.get("/sync")
@app.post("/sync")
def disparar_sync(request: Request):
    _verificar_token(request)

    force = request.query_params.get("force", "false").lower() == "true"

    if sync_engine.state.rodando and not force:
        return JSONResponse({
            "ok": False,
            "msg": "Já está rodando.",
            "status": sync_engine.state.to_dict()
        })

    def _rodar():
        result = sync_engine.rodar_sync(force=force)
        log.info(f"[Sync] Resultado: {result}")

    t = threading.Thread(target=_rodar, daemon=True)
    t.start()

    msg = "Sincronização iniciada do zero." if force else "Sincronização iniciada (retoma se interrompida)."
    return {"ok": True, "msg": msg, "acompanhe": "/status"}


@app.get("/fix-omie")
def fix_omie(request: Request):
    _verificar_token(request)
    if sync_engine.state.rodando:
        return {"ok": False, "msg": "Sync rodando. Aguarde."}

    def _rodar():
        sync_engine._corrigir_omie()

    threading.Thread(target=_rodar, daemon=True).start()
    return {"ok": True, "msg": "Correção Base Omie iniciada.", "acompanhe": "/status"}


@app.get("/sync-omie")
def sync_omie(request: Request):
    _verificar_token(request)
    if sync_engine.state.rodando:
        return {"ok": False, "msg": "Sync rodando. Aguarde."}

    def _rodar():
        sync_engine._reprocessar_omie()

    threading.Thread(target=_rodar, daemon=True).start()
    return {"ok": True, "msg": "Reprocessamento total da Base Omie iniciado.", "acompanhe": "/status"}


@app.on_event("startup")
def on_startup():
    """Se o processo reiniciou no meio de um sync, retoma automaticamente."""
    if sync_engine.state.rodando and sync_engine.state.fase not in ("", "done"):
        log.info("[Startup] Sync interrompido detectado — retomando automaticamente...")
        t = threading.Thread(target=sync_engine.rodar_sync, daemon=True)
        t.start()