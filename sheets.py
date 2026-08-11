"""
sheets.py — acesso à planilha via Google Sheets API
Usa Service Account (JSON em env var GOOGLE_SA_JSON)
"""
import os, json, time, logging
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

def _sheet_id(nome_aba: str) -> int | None:
    svc  = _get_service()
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == nome_aba:
            return s["properties"]["sheetId"]
    return None

def ler_aba(nome_aba: str, range_: str = None) -> list:
    svc = _get_service()
    range_completo = f"'{nome_aba}'!{range_}" if range_ else f"'{nome_aba}'"
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_completo,
        valueRenderOption="UNFORMATTED_VALUE"
    ).execute()
    return result.get("values", [])

def limpar_aba(nome_aba: str):
    svc = _get_service()
    svc.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{nome_aba}'"
    ).execute()

def escrever_aba(nome_aba: str, valores: list, inicio: str = "A1", max_retries: int = 3):
    if not valores:
        return
    svc = _get_service()
    for tentativa in range(1, max_retries + 1):
        try:
            svc.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{nome_aba}'!{inicio}",
                valueInputOption="RAW",
                body={"values": valores}
            ).execute()
            return
        except Exception as e:
            log.warning(f"[Sheets] escrever_aba tentativa {tentativa} falhou: {e}")
            if tentativa < max_retries:
                time.sleep(5 * tentativa)
            else:
                raise

def append_aba(nome_aba: str, valores: list, max_retries: int = 3):
    if not valores:
        return
    svc = _get_service()
    for tentativa in range(1, max_retries + 1):
        try:
            svc.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{nome_aba}'!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": valores}
            ).execute()
            return
        except Exception as e:
            log.warning(f"[Sheets] append_aba tentativa {tentativa} falhou: {e}")
            if tentativa < max_retries:
                time.sleep(5 * tentativa)
            else:
                raise

def limpar_formatacao_dados(nome_aba: str, linha_inicio: int = 2):
    """Remove formatação herdada do cabeçalho nas linhas de dados."""
    svc      = _get_service()
    sheet_id = _sheet_id(nome_aba)
    if sheet_id is None:
        return
    try:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId":          sheet_id,
                        "startRowIndex":    linha_inicio - 1,
                        "endRowIndex":      10000,
                        "startColumnIndex": 0,
                        "endColumnIndex":   60
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                            "textFormat": {
                                "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                                "bold": False
                            }
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                }
            }]}
        ).execute()
        log.info(f"[Sheets] Formatação limpa em '{nome_aba}'.")
    except Exception as e:
        log.warning(f"[Sheets] Erro ao limpar formatação de '{nome_aba}': {e}")

def garantir_aba(nome_aba: str, cabecalho: list):
    """
    Garante que a aba existe. NÃO sobrescreve cabeçalho se a aba já existe —
    preserva cabeçalhos customizados da planilha.
    Só grava cabeçalho em abas criadas do zero.
    """
    svc  = _get_service()
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    abas = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if nome_aba not in abas:
        # Cria aba nova e grava cabeçalho
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": nome_aba}}}]}
        ).execute()
        escrever_aba(nome_aba, [cabecalho], "A1")
        log.info(f"[Sheets] Aba '{nome_aba}' criada com cabeçalho.")
    else:
        log.info(f"[Sheets] Aba '{nome_aba}' já existe — cabeçalho preservado.")

    # Limpa formatação herdada nas linhas de dados
    limpar_formatacao_dados(nome_aba, linha_inicio=2)