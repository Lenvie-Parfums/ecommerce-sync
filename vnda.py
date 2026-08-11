"""
vnda.py — chamadas à API Vnda
"""
import os, logging, requests

log = logging.getLogger(__name__)

BASE_URL   = os.getenv("VNDA_BASE_URL", "https://api.vnda.com.br").rstrip("/")
TOKEN      = os.getenv("VNDA_TOKEN")
SHOP_HOST  = os.getenv("VNDA_SHOP_HOST", "www.lenvieparfums.com")

def _get(path: str) -> dict | None:
    try:
        resp = requests.get(
            BASE_URL + path,
            headers={
                "authorization": TOKEN,
                "x-shop-host":   SHOP_HOST,
                "content-type":  "application/json"
            },
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"[Vnda] GET {path} → {resp.status_code}")
        return None
    except Exception as e:
        log.warning(f"[Vnda] Erro {path}: {e}")
        return None

def buscar_pedido(order_code: str) -> dict | None:
    """Retorna dados do pedido na Vnda."""
    return _get(f"/api/v2/orders/{order_code}")

def extrair_dados(order_code: str) -> dict:
    """Extrai cidade, estado, receita e transportadora de um pedido Vnda."""
    dados = {"cidade": "", "estado": "", "receita": "", "transp": ""}
    if not order_code:
        return dados
    vo = buscar_pedido(order_code)
    if not vo:
        return dados
    addr = vo.get("shipping_address") or {}
    dados["cidade"]  = addr.get("city",  "")
    dados["estado"]  = addr.get("state", "")
    dados["receita"] = vo.get("revenue") or vo.get("total", "")
    dados["transp"]  = vo.get("shipping_method_name", "")
    return dados
