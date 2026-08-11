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
    1:   "PEDIDO RECEBIDO",
    3:   "AGUARDANDO WMS",
    5:   "AGUARDANDO PICKING",
    8:   "AGUARDANDO NOTA",
    9:   "LIBERADO PARA CORTE",
    10:  "PICKING DIGITAL REALIZADO",
    13:  "CANCELADO",
    20:  "CHECKOUT",
    25:  "NOTA RECEBIDA",
    28:  "RASTREADOR RECEBIDO",
    30:  "PEDIDO SEPARADO",
    50:  "DESPACHADO",
    60:  "COLETADO",
    70:  "EM TRANSITO",
    75:  "SAIU PARA ENTREGA",
    80:  "OCORRÊNCIA",
    90:  "ENTREGUE",
    100: "FALHA NA ENTREGA",
    110: "RECUSADO",
    200: "CANCELADO",
    300: "DEVOLVIDO",
    400: "EXTRAVIADO",
    411: "ROUBO DE CARGA",
    500: "REDESPACHO",
    510: "REGISTROS TRANSPORTADORA",
    1002:"SERIAIS DEFINIDOS",
    1010:"ENDERECO INCORRETO",
    1020:"DESTINATARIO AUSENTE",
    1040:"AGUARDANDO RETIRADA",
    1100:"NAO PROCURADO",
    1150:"VOLUME PREPARADO",
    1199:"AGUARDANDO CTE",
    1200:"CTE GERADO",
    9999:"EM TRATATIVA",
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
        str(id_tpl), "NORMAL", "OMIE", inv.get("nature", ""), # NATUREZA_DE_OPERACAO
        str(order_num or inf.get("number", "")),
        "NAO", "0", inf.get("priority", ""), # PRIORIDADE
        inv.get("number", ""),   inv.get("value", ""),
        inv.get("series", ""),   inv.get("key", ""),
        inv.get("emission", ""),
        sh.get("nick", ""),      sh.get("method", ""),
        sh.get("vol", ""),       wms.get("weight", ""),
        wms.get("vol", ""),      inf.get("channel", ""), inf.get("marketplace", ""), # VOL.OP, CANAL VENDA, MKP NOME
        sh.get("trackerUrl") or sh.get("trackerurl", ""),
        sh.get("tracker", ""),
        sh.get("route", ""), inf.get("iderp", ""), # CODIGO DE ROTA, ID-MKP
        inf.get("date", ""),     inf.get("prediction", ""),
        status_texto(o.get("code")),
        ult.get("message", ""),
        "SIM" if evs else "NAO",
        f"{ult.get('dtshipping', '')} - {ult.get('message', '')}" if ult.get("dtshipping") else "",
        dest.get("to") or dest.get("name") or dest.get("recipient", ""),
        dest.get("mail") or dest.get("email", ""),
        dest.get("phone") or dest.get("telephone", ""),
        dest.get("zipcode") or dest.get("cep", ""),
        dest.get("state") or dest.get("uf", ""), 
        dest.get("region", ""), dest.get("macroRegion", ""), # REGIAO, GRANDE REGIAO
        ev.get("created", ""),       ev.get("os", ""),
        ev.get("invoice", ""),       ev.get("startPicking", ""),
        ev.get("startCheckout", ""), ev.get("dispatched", ""),
        ev.get("in_transit", ""),    ev.get("fail", ""),
        dias_ult,
        ult.get("dtshipping", ""),
        ev.get("delivered", ""),     ev.get("cancelled", ""),
        ev.get("user", "") # POR_QUEM
    ]
