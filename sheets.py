"""
sheets.py — acesso à planilha via Google Sheets API
Usa Service Account (JSON em env var GOOGLE_SA_JSON)
"""
import os, json, logging
from google.oauth2 import service_account
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1F2f8u9B7zK78bt_8-pRVfAjD4uaHJ3y_JrMjsKCxAoo")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_service = None

def _get_service():
    global _service
    if _service:
        return _service
    sa_json = os.getenv("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError("GOOGLE_SA_JSON não configurado.")
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    _service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _service

def ler_aba(nome_aba: str, range_: str = None) -> list:
    """Retorna lista de listas com os valores da aba."""
    svc = _get_service()
    range_completo = f"'{nome_aba}'!{range_}" if range_ else f"'{nome_aba}'"
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_completo,
        valueRenderOption="UNFORMATTED_VALUE"
    ).execute()
    return result.get("values", [])

def limpar_aba(nome_aba: str):
    """Limpa todo o conteúdo da aba."""
    svc = _get_service()
    svc.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{nome_aba}'"
    ).execute()

def escrever_aba(nome_aba: str, valores: list, inicio: str = "A1"):
    """Escreve lista de listas na aba a partir de inicio."""
    if not valores:
        return
    svc = _get_service()
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{nome_aba}'!{inicio}",
        valueInputOption="RAW",
        body={"values": valores}
    ).execute()

def append_aba(nome_aba: str, valores: list):
    """Faz append de linhas no final da aba."""
    if not valores:
        return
    svc = _get_service()
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{nome_aba}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": valores}
    ).execute()

def garantir_aba(nome_aba: str, cabecalho: list):
    """Garante que a aba existe e tem o cabeçalho correto."""
    svc = _get_service()

    # Verifica se aba existe
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    abas = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if nome_aba not in abas:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": nome_aba}}}]}
        ).execute()
        log.info(f"[Sheets] Aba '{nome_aba}' criada.")

    # Verifica cabeçalho
    atual = ler_aba(nome_aba, "A1:ZZ1")
    if not atual or atual[0] != cabecalho:
        escrever_aba(nome_aba, [cabecalho], "A1")
        log.info(f"[Sheets] Cabeçalho de '{nome_aba}' gravado.")
