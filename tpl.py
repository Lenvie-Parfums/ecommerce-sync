"""
tpl.py — chamadas à API TPL (oms.tpl.com.br/api)
"""
import os, time, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

BASE_URL   = os.getenv("TPL_BASE_URL", "https://oms.tpl.com.br/api")
TPL_APIKEY = os.getenv("TPL_APIKEY")
TPL_TOKEN  = os.getenv("TPL_TOKEN")
TPL_EMAIL  = os.getenv("TPL_EMAIL")
TZ_SP      = ZoneInfo("America/Sao_Paulo")

_auth_cache    = None
_auth_cache_ts = 0

STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO", "EXTRAVIADO", "RECUSADO"}

STATUS_MAP = {
    1:    "PEDIDO RECEBIDO",
    3:    "AGUARDANDO WMS",
    5:    "AGUARDANDO PICKING",
    8:    "AGUARDANDO NOTA",
    9:    "LIBERADO PARA CORTE",
    10:   "PICKING DIGITAL REALIZADO",
    13:   "CANCELADO",
    20:   "CHECKOUT",
    25:   "NOTA RECEBIDA",
    28:   "RASTREADOR RECEBIDO",
    30:   "PEDIDO SEPARADO",
    50:   "DESPACHADO",
    60:   "COLETADO",
    70:   "EM TRANSITO",
    75:   "SAIU PARA ENTREGA",
    80:   "OCORRÊNCIA",
    90:   "ENTREGUE",
    100:  "FALHA NA ENTREGA",
    110:  "RECUSADO",
    200:  "CANCELADO",
    300:  "DEVOLVIDO",
    400:  "EXTRAVIADO",
    411:  "ROUBO DE CARGA",
    500:  "REDESPACHO",
    510:  "REGISTROS TRANSPORTADORA",
    1002: "SERIAIS DEFINIDOS",
    1010: "ENDERECO INCORRETO",
    1020: "DESTINATARIO AUSENTE",
    1040: "AGUARDANDO RETIRADA",
    1100: "NAO PROCURADO",
    1150: "VOLUME PREPARADO",
    1199: "AGUARDANDO CTE",
    1200: "CTE GERADO",
    9999: "EM TRATATIVA",
    10002:"AVARIA",
    10003:"AVISO COLETA",
    10004:"PARADO POSTO FISCAL",
    10006:"VOLUMES AJUSTADO WMS",
    10007:"PESO AJUSTADO WMS",
    10008:"ERRO ENDERECO",
}

def status_texto(code):
    try:
        return STATUS_MAP.get(int(code or 0), f"STATUS {code}")
    except Exception:
        return f"STATUS {code}"

def hoje_str():
    return datetime.now(TZ_SP).strftime("%-d/%-m/%Y")

def autenticar():
    global _auth_cache, _auth_cache_ts
    agora = time.time()
    if _auth_cache and (agora - _auth_cache_ts) < 55 * 60:
        return _auth_cache

    resp = requests.post(f"{BASE_URL}/get/auth", json={
        "apikey": TPL_APIKEY,
        "token":  TPL_TOKEN,
        "email":  TPL_EMAIL,
    }, timeout=30)
    data = resp.json()
    auth = data.get("token") or data.get("auth")

    if resp.status_code == 200 and auth:
        _auth_cache    = auth
        _auth_cache_ts = agora
        log.info("[TPL] Auth ok.")
        return auth

    if data.get("code") == 400 and _auth_cache:
        log.warning("[TPL] Auth 400 — reutilizando cache.")
        return _auth_cache

    raise RuntimeError(f"[TPL] Auth falhou: {data}")

def post(endpoint: str, body: dict) -> dict:
    try:
        resp = requests.post(f"{BASE_URL}{endpoint}", json=body, timeout=60)
        return resp.json()
    except Exception as e:
        log.warning(f"[TPL] {endpoint} erro: {e}")
        return {"code": 0}

def listar_pedidos(data_inicio: str) -> list:
    auth = autenticar()
    data = post("/get/list", {"auth": auth, "begin": data_inicio, "end": hoje_str()})
    if data.get("code") != 200 or not data.get("list"):
        log.warning(f"[TPL] get/list sem resultado: {data}")
        return []
    log.info(f"[TPL] {len(data['list'])} pedidos na lista.")
    return data["list"]

def detalhe_pedido(id_tpl: int, auth: str) -> dict | None:
    data = post("/get/orderdetail", {"auth": auth, "order": {"id": id_tpl}})
    if data.get("code") != 200 or not data.get("order"):
        return None
    return data["order"]

def montar_linha_tpl(id_tpl, order_num, auth) -> list | None:
    """
    Monta linha para a Base TPL — 55 colunas alinhadas ao cabeçalho original:
    0  ID                        | 1  TIPO                    | 2  INTEGRACAO
    3  NATUREZA_DE_OPERACAO (M)  | 4  PEDIDO                  | 5  ANEXO
    6  OS                        | 7  PRIORIDADE (M)          | 8  NF
    9  VALOR_NOTA                | 10 SERIE                   | 11 CHAVE
    12 NF_EMISSAO                | 13 TRANSPORTADORA          | 14 MODALIDADE
    15 VOL.NF                    | 16 PESO (G)                | 17 VOL.OP
    18 CANAL VENDA (M)           | 19 MKP NOME (M)            | 20 URL
    21 SRO                       | 22 CODIGO DE ROTA (M)      | 23 ID-MKP
    24 PREVISAO ORIGINAL         | 25 PREVISAO AJUSTADA       | 26 SITUACAO
    27 DETALHE                   | 28 TEM_OCORRENCIA          | 29 ULTIMA_OCORRENCIA_NO_PEDIDO
    30 DEST_NOME                 | 31 DEST_EMAIL              | 32 DEST_FONE
    33 DEST_CEP                  | 34 UF                      | 35 REGIAO (M)
    36 GRANDE REGIAO (M)         | 37 DH/INC                  | 38 DH/WMS
    39 DH/NOTA                   | 40 DH/PICKING              | 41 DH/CHECKOUT
    42 DH/DESPACHADO             | 43 DH/COLETA               | 44 DH/FALHA
    45 DIAS_ULTIMA_MOVIMENTACAO  | 46 DH/ULTIMA_MOVIMENTACAO  | 47 DH/ENTREGA
    48 DH/CANCELADO              | 49 POR_QUEM (M)            | 50 MOTIVO (M)
    51 ADVERTENCIA (M)           | 52 SEM ESTOQUE (M)         | 53 EMBALAGEM
    54 UNIDADE
    (M) = manual, script grava "" mas preserva no update seletivo
    """
    o = detalhe_pedido(id_tpl, auth)
    if not o:
        return None

    # info
    inf = o.get("info", {})
    if isinstance(inf, list):
        inf = inf[0] if inf else {}

    # shippment
    sh = o.get("shippment", {}) or {}
    if isinstance(sh, list):
        sh = sh[0] if sh else {}

    # invoice vem como lista
    _inv = o.get("invoice", [])
    inv  = _inv[0] if isinstance(_inv, list) and _inv else (_inv or {})

    # internalevents
    ev = o.get("internalevents", {})
    if isinstance(ev, list):
        ev = ev[0] if ev else {}

    # shippingevents
    evs = o.get("shippingevents", []) or []
    if not isinstance(evs, list):
        evs = []
    ult = evs[-1] if evs else {}

    # wms vem como lista
    _wms = o.get("wms", [])
    wms  = _wms[0] if isinstance(_wms, list) and _wms else (_wms or {})

    # deliveryTo
    _dest = o.get("deliveryTo", {})
    dest  = _dest[0] if isinstance(_dest, list) else (_dest or {})

    dias_ult = ""
    if ult.get("dtshipping"):
        try:
            p = ult["dtshipping"].split(" ")[0].split("/")
            d = datetime(int(p[2]), int(p[1]), int(p[0]), tzinfo=TZ_SP)
            dias_ult = (datetime.now(TZ_SP) - d).days
        except Exception:
            pass

    return [
        str(id_tpl),                                          # 0  ID
        "NORMAL",                                             # 1  TIPO
        "OMIE",                                               # 2  INTEGRACAO
        "",                                                   # 3  NATUREZA_DE_OPERACAO (manual)
        str(order_num or inf.get("number", "")),              # 4  PEDIDO
        "NAO",                                                # 5  ANEXO
        "0",                                                  # 6  OS
        "",                                                   # 7  PRIORIDADE (manual)
        inv.get("number", ""),                                # 8  NF
        inv.get("value", ""),                                 # 9  VALOR_NOTA
        inv.get("series", ""),                                # 10 SERIE
        inv.get("key", ""),                                   # 11 CHAVE
        inv.get("emission", ""),                              # 12 NF_EMISSAO
        sh.get("nick", ""),                                   # 13 TRANSPORTADORA
        sh.get("method", ""),                                 # 14 MODALIDADE
        sh.get("vol", ""),                                    # 15 VOL.NF
        wms.get("weight", ""),                                # 16 PESO (G)
        "",                                                   # 17 VOL.OP (não vem na API)
        "",                                                   # 18 CANAL VENDA (manual)
        "",                                                   # 19 MKP NOME (manual)
        sh.get("trackerUrl") or sh.get("trackerurl", ""),    # 20 URL
        sh.get("tracker", ""),                                # 21 SRO
        "",                                                   # 22 CODIGO DE ROTA (manual)
        inf.get("iderp", ""),                                 # 23 ID-MKP
        inf.get("date", ""),                                  # 24 PREVISAO ORIGINAL
        inf.get("prediction", ""),                            # 25 PREVISAO AJUSTADA
        status_texto(o.get("code")),                          # 26 SITUACAO
        ult.get("message", ""),                               # 27 DETALHE
        "SIM" if evs else "NAO",                              # 28 TEM_OCORRENCIA
        f"{ult.get('dtshipping','')} - {ult.get('message','')}" if ult.get("dtshipping") else "",  # 29 ULTIMA_OCORRENCIA_NO_PEDIDO
        dest.get("to") or dest.get("name") or dest.get("recipient", ""),  # 30 DEST_NOME
        dest.get("mail") or dest.get("email", ""),            # 31 DEST_EMAIL
        dest.get("phone") or dest.get("telephone", ""),       # 32 DEST_FONE
        str(dest.get("zipcode") or dest.get("cep", "")).zfill(8) if (dest.get("zipcode") or dest.get("cep")) else "",  # 33 DEST_CEP (8 dígitos com zero à esquerda)
        dest.get("state") or dest.get("uf", ""),              # 34 UF
        "",                                                   # 35 REGIAO (manual)
        "",                                                   # 36 GRANDE REGIAO (manual)
        ev.get("created", ""),                                # 37 DH/INC
        ev.get("os", ""),                                     # 38 DH/WMS
        ev.get("invoice", ""),                                # 39 DH/NOTA
        ev.get("startPicking", ""),                           # 40 DH/PICKING
        ev.get("startCheckout", ""),                          # 41 DH/CHECKOUT
        ev.get("dispatched", ""),                             # 42 DH/DESPACHADO
        ev.get("in_transit", ""),                             # 43 DH/COLETA
        ev.get("fail", ""),                                   # 44 DH/FALHA
        dias_ult,                                             # 45 DIAS_ULTIMA_MOVIMENTACAO
        ult.get("dtshipping", ""),                            # 46 DH/ULTIMA_MOVIMENTACAO
        ev.get("delivered", ""),                              # 47 DH/ENTREGA
        ev.get("cancelled", ""),                              # 48 DH/CANCELADO
        "",                                                   # 49 POR_QUEM (manual)
        "",                                                   # 50 MOTIVO (manual)
        "",                                                   # 51 ADVERTENCIA (manual)
        "",                                                   # 52 SEM ESTOQUE (manual)
        wms.get("packaging", "") or wms.get("embalagem", ""),# 53 EMBALAGEM
        wms.get("unit", "") or wms.get("unidade", ""),       # 54 UNIDADE
    ]