"""
tpl.py — chamadas à API TPL (oms.tpl.com.br/api)
"""
import os, json, time, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

BASE_URL  = os.getenv("TPL_BASE_URL", "https://oms.tpl.com.br/api")
TPL_APIKEY= os.getenv("TPL_APIKEY")
TPL_TOKEN = os.getenv("TPL_TOKEN")
TPL_EMAIL = os.getenv("TPL_EMAIL")
TZ_SP     = ZoneInfo("America/Sao_Paulo")

_auth_cache    = None
_auth_cache_ts = 0

STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO", "EXTRAVIADO", "RECUSADO"}

STATUS_MAP = {
    1:"PEDIDO RECEBIDO", 3:"AGUARDANDO WMS", 5:"AGUARDANDO PICKING",
    8:"AGUARDANDO NOTA", 13:"CANCELADO", 20:"CHECKOUT", 25:"NOTA RECEBIDA",
    30:"PEDIDO SEPARADO", 50:"DESPACHADO", 60:"COLETADO",
    70:"EM TRANSITO", 75:"SAIU PARA ENTREGA", 80:"OCORRÊNCIA",
    90:"ENTREGUE", 100:"FALHA NA ENTREGA", 110:"RECUSADO",
    200:"CANCELADO", 300:"DEVOLVIDO", 400:"EXTRAVIADO"
}

def status_texto(code):
    return STATUS_MAP.get(int(code or 0), f"STATUS {code}")

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
    """Retorna lista de {id, order, date} desde data_inicio."""
    auth = autenticar()
    data = post("/get/list", {"auth": auth, "begin": data_inicio, "end": hoje_str()})
    if data.get("code") != 200 or not data.get("list"):
        log.warning(f"[TPL] get/list sem resultado: {data}")
        return []
    log.info(f"[TPL] {len(data['list'])} pedidos na lista.")
    return data["list"]

def detalhe_pedido(id_tpl: int, auth: str) -> dict | None:
    """Retorna detalhe completo de um pedido."""
    data = post("/get/orderdetail", {"auth": auth, "order": {"id": id_tpl}})
    if data.get("code") != 200 or not data.get("order"):
        return None
    return data["order"]

def montar_linha_tpl(id_tpl, order_num, auth) -> list | None:
    """Monta linha completa para a aba Base TPL."""
    o = detalhe_pedido(id_tpl, auth)
    if not o:
        return None

    inf = o.get("info", {})
    if isinstance(inf, list):
        inf = inf[0] if inf else {}
    sh  = o.get("shippment",       {})
    inv = o.get("invoice",         {})
    ev  = o.get("internalevents",  {})
    if isinstance(ev, list):
        ev = ev[0] if ev else {}
    evs = o.get("shippingevents",  []) or []
    ult = evs[-1] if evs else {}
    _dest = o.get("deliveryTo", {})
    dest  = _dest[0] if isinstance(_dest, list) else (_dest or {})
    wms = o.get("wms",             {}) or {}

    dias_ult = ""
    if ult.get("dtshipping"):
        try:
            p = ult["dtshipping"].split(" ")[0].split("/")
            d = datetime(int(p[2]), int(p[1]), int(p[0]), tzinfo=TZ_SP)
            dias_ult = (datetime.now(TZ_SP) - d).days
        except Exception:
            pass

    return [
        str(id_tpl), "NORMAL", "OMIE", "",
        str(order_num or inf.get("number", "")),
        "NAO", "0", "",
        inv.get("number", ""),  inv.get("value", ""),
        inv.get("series", ""),  inv.get("key", ""),
        inv.get("emission", ""),
        sh.get("nick", ""),     sh.get("method", ""),
        sh.get("vol", ""),      wms.get("weight", ""),
        sh.get("vol", ""),      "", "",
        sh.get("trackerurl", ""), sh.get("tracker", ""),
        "", inf.get("iderp", ""),
        inf.get("date", ""),    inf.get("prediction", ""),
        status_texto(o.get("code")),
        ult.get("message", ""),
        "SIM" if evs else "NAO",
        f"{ult.get('dtshipping','')} - {ult.get('message','')}" if ult.get("dtshipping") else "",
        dest.get("name") or dest.get("recipient", ""),
        dest.get("email", ""),
        dest.get("phone") or dest.get("telephone", ""),
        dest.get("zipcode") or dest.get("cep", ""),
        dest.get("state") or dest.get("uf", ""), "", "",
        ev.get("created", ""),      ev.get("os", ""),
        ev.get("invoice", ""),      ev.get("startPicking", ""),
        ev.get("startCheckout", ""),ev.get("dispatched", ""),
        ev.get("in_transit", ""),   ev.get("fail", ""),
        dias_ult,
        ult.get("dtshipping", ""),
        ev.get("delivered", ""),    ev.get("cancelled", ""),
        ""
    ]
