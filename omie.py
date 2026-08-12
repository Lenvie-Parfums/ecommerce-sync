"""
omie.py — chamadas ao Omie ERP
"""
import os, time, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

OMIE_PEDIDO_URL = "https://app.omie.com.br/api/v1/produtos/pedido/"
OMIE_NF_URL     = "https://app.omie.com.br/api/v1/produtos/nfconsultar/"
APP_KEY    = os.getenv("APP_KEY_OMIE")
APP_SECRET = os.getenv("APP_SECRET_OMIE")
TZ_SP      = ZoneInfo("America/Sao_Paulo")

def _post(url: str, call: str, param: dict, retries: int = 3) -> dict:
    payload = {"call": call, "app_key": APP_KEY, "app_secret": APP_SECRET, "param": [param]}
    for t in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            texto = resp.text
            if "REDUNDANT" in texto or "MISUSE_API" in texto or resp.status_code in (425, 429):
                log.warning("[Omie] Rate limit. Aguardando 60s...")
                time.sleep(60)
                continue
            return resp.json()
        except Exception as e:
            log.warning(f"[Omie] Erro tentativa {t}: {e}")
            time.sleep(10)
    return {}

def listar_pedidos(data_inicio: str) -> list:
    hoje = datetime.now(TZ_SP).strftime("%d/%m/%Y")
    resultados = []
    vistos = set()

    for etapa in ["60", "70"]:
        pagina = 1
        total_pg = 1
        while pagina <= total_pg:
            data = _post(OMIE_PEDIDO_URL, "ListarPedidos", {
                "pagina": pagina,
                "registros_por_pagina": 50,
                "apenas_importado_api": "N",
                "etapa": etapa,
                "filtrar_por_data_de":  data_inicio,
                "filtrar_por_data_ate": hoje,
                "filtrar_apenas_inclusao": "S"
            })
            if data.get("faultstring"):
                log.warning(f"[Omie] Etapa {etapa} pág {pagina}: {data['faultstring']}")
                break

            total_pg = data.get("total_de_paginas", 1)
            pedidos  = data.get("pedido_venda_produto", [])

            for ped in pedidos:
                cab = ped.get("cabecalho", {})
                num = str(cab.get("numero_pedido", ""))
                if num in vistos:
                    continue
                vistos.add(num)
                resultados.append({
                    "numero":        num,
                    "codigo_pedido": str(cab.get("codigo_pedido", "")),
                    "order_code":    cab.get("codigo_pedido_integracao", "").strip(),
                    "data":          cab.get("data_previsao", ""),
                    "etapa":         etapa,
                    "nf":            "",
                    "rastreio":      "",
                    "transp":        "",
                })

            log.info(f"[Omie] Etapa {etapa} | Pág {pagina}/{total_pg}: {len(pedidos)} pedidos")
            pagina += 1
            time.sleep(0.7)

    log.info(f"[Omie] Total: {len(resultados)} pedidos na base. NF será buscada sob demanda.")
    return resultados

def buscar_nf(codigo_pedido: str) -> str:
    if not codigo_pedido:
        return ""
    try:
        data = _post(OMIE_NF_URL, "ConsultarNF", {
            "nIdPedido": int(codigo_pedido) if str(codigo_pedido).isdigit() else codigo_pedido
        })
        if data.get("faultstring"):
            return ""
        
        num_nf = str(data.get("ide", {}).get("nNF", ""))
        # Converte para int e depois str para remover zeros à esquerda (ex: 000123 -> 123)
        return str(int(num_nf)) if num_nf.isdigit() else num_nf
    except Exception as e:
        log.warning(f"[Omie] Erro buscar NF para {codigo_pedido}: {e}")
        return ""
