"""
omie.py — chamadas ao Omie ERP
"""
import os, json, time, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

OMIE_URL   = "https://app.omie.com.br/api/v1/produtos/pedido/"
APP_KEY    = os.getenv("APP_KEY_OMIE")
APP_SECRET = os.getenv("APP_SECRET_OMIE")
TZ_SP      = ZoneInfo("America/Sao_Paulo")

def _post(call: str, param: dict, retries: int = 3) -> dict:
    payload = {"call": call, "app_key": APP_KEY, "app_secret": APP_SECRET, "param": [param]}
    for t in range(1, retries + 1):
        try:
            resp = requests.post(OMIE_URL, json=payload, timeout=60)
            texto = resp.text
            if "REDUNDANT" in texto or "MISUSE_API" in texto or resp.status_code in (425, 429):
                log.warning(f"[Omie] Rate limit. Aguardando 60s...")
                time.sleep(60)
                continue
            return resp.json()
        except Exception as e:
            log.warning(f"[Omie] Erro tentativa {t}: {e}")
            time.sleep(10)
    return {}

def listar_pedidos(data_inicio: str) -> list:
    """
    Retorna todos os pedidos etapas 60+70 desde data_inicio.
    Retorna lista de dicts com campos do cabecalho + frete.
    """
    hoje = datetime.now(TZ_SP).strftime("%d/%m/%Y")
    resultados = []
    vistos = set()

    for etapa in ["60", "70"]:
        pagina = 1
        total_pg = 1
        while pagina <= total_pg:
            data = _post("ListarPedidos", {
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
                cab   = ped.get("cabecalho", {})
                frete = ped.get("frete", {})
                info  = ped.get("informacoes_adicionais", {})
                num   = str(cab.get("numero_pedido", ""))
                if num in vistos:
                    continue
                vistos.add(num)
                resultados.append({
                    "numero":     num,
                    "order_code": cab.get("codigo_pedido_integracao", "").strip(),
                    "data":       cab.get("data_pedido", ""),
                    "etapa":      etapa,
                    "nf":         info.get("numero_nota", ""),
                    "rastreio":   frete.get("codigo_rastreio", "") or frete.get("link_rastreio", ""),
                    "transp":     str(frete.get("codigo_transportadora", "") or ""),
                })

            log.info(f"[Omie] Etapa {etapa} | Pág {pagina}/{total_pg}: {len(pedidos)} pedidos")
            pagina += 1
            time.sleep(0.7)

    log.info(f"[Omie] Total: {len(resultados)} pedidos.")
    return resultados
