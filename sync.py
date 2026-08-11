"""
sync.py — motor de sincronização INCREMENTAL
Lógica:
  1. Puxa lista de pedidos TPL (get/list) desde DATA_INICIO
  2. Lê o que já está na planilha (chave = número do pedido)
  3. Pula pedidos com status final (ENTREGUE, CANCELADO, etc.)
  4. Para pedidos novos ou em aberto: chama get/orderdetail e grava
  5. Atualiza Base Omie e Pedidos Site de forma incremental
  6. Recalcula aba Status
"""
import time, logging
from datetime import datetime
from zoneinfo import ZoneInfo

import tpl, omie, vnda, sheets

log = logging.getLogger(__name__)

DATA_INICIO = "30/7/2026"
TZ_SP       = ZoneInfo("America/Sao_Paulo")

# Cabeçalhos das abas
CAB_TPL = [
    "ID","TIPO","INTEGRACAO","NATUREZA_DE_OPERACAO","PEDIDO","ANEXO","OS","PRIORIDADE",
    "NF","VALOR_NOTA","SERIE","CHAVE","NF_EMISSAO","TRANSPORTADORA","MODALIDADE",
    "VOL.NF","PESO (G)","VOL.OP","CANAL VENDA","MKP NOME","URL","SRO",
    "CODIGO DE ROTA","ID-MKP","PREVISAO ORIGINAL","PREVISAO AJUSTADA",
    "SITUACAO","DETALHE","TEM_OCORRENCIA","ULTIMA_OCORRENCIA",
    "DEST_NOME","DEST_EMAIL","DEST_FONE","DEST_CEP","UF","REGIAO","GRANDE REGIAO",
    "DH/INC","DH/WMS","DH/NOTA","DH/PICKING","DH/CHECKOUT","DH/DESPACHADO",
    "DH/COLETA","DH/FALHA","DIAS_ULTIMA_MOV","DH/ULTIMA_MOV",
    "DH/ENTREGA","DH/CANCELADO","POR_QUEM"
]
CAB_OMIE = [
    "Data de Emissão","Nota Fiscal","Operação","Situação","Vendedor","Código de Integração"
]
CAB_SITE = [
    "Data do Pedido","Nº Pedido (Olist)","Nota Fiscal (Omie)",
    "Cidade","Estado","Receita","Transportadora",
    "Dias do Pedido","Contato com Cliente?",
    "Status TPL","Data Previsão Entrega","Data Ajustada Entrega",
    "Data de Entrega","Rastreio TPL"
]

COL_PEDIDO  = CAB_TPL.index("PEDIDO")
COL_SITUAC  = CAB_TPL.index("SITUACAO")
COL_VALOR   = CAB_TPL.index("VALOR_NOTA")

# Status que não precisam ser reconsultados
STATUS_FINAIS = {"ENTREGUE","CANCELADO","DEVOLVIDO","EXTRAVIADO","RECUSADO"}


# ── Estado global do sync (em memória) ───────────────────────
class SyncState:
    rodando: bool = False
    inicio:  str  = ""
    etapa:   str  = ""
    total:   int  = 0
    atual:   int  = 0
    erros:   int  = 0
    fim:     str  = ""

state = SyncState()


# ── Helpers ──────────────────────────────────────────────────
def _mapear_base_tpl() -> dict:
    """
    Lê a Base TPL e retorna {numero_pedido: {"linha_idx": int, "status": str}}
    linha_idx é 0-based a partir dos dados (sem cabeçalho).
    """
    dados = sheets.ler_aba("Base TPL")
    if len(dados) <= 1:
        return {}
    mapa = {}
    for i, row in enumerate(dados[1:], start=1):  # pula cabeçalho
        num = str(row[COL_PEDIDO]).strip() if len(row) > COL_PEDIDO else ""
        sit = str(row[COL_SITUAC]).upper().strip() if len(row) > COL_SITUAC else ""
        if num:
            mapa[num] = {"linha_idx": i, "status": sit}
    return mapa

def _dias_desde(data_str: str) -> int | str:
    if not data_str:
        return ""
    try:
        p = data_str.split(" ")[0].split("/")
        d = datetime(int(p[2]), int(p[1]), int(p[0]), tzinfo=TZ_SP)
        return (datetime.now(TZ_SP) - d).days
    except Exception:
        return ""


# ── Sincronização principal ───────────────────────────────────
def rodar_sync() -> dict:
    if state.rodando:
        return {"ok": False, "msg": "Já está rodando."}

    state.rodando = True
    state.inicio  = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
    state.fim     = ""
    state.erros   = 0

    try:
        # 1. Garante abas com cabeçalho
        state.etapa = "garantindo abas"
        sheets.garantir_aba("Base TPL",    CAB_TPL)
        sheets.garantir_aba("Base Omie",   CAB_OMIE)
        sheets.garantir_aba("Pedidos Site", CAB_SITE)

        # 2. Lista pedidos TPL
        state.etapa = "listando pedidos TPL"
        log.info("[Sync] Listando pedidos TPL...")
        lista_tpl = tpl.listar_pedidos(DATA_INICIO)
        state.total = len(lista_tpl)
        log.info(f"[Sync] {state.total} pedidos na lista TPL.")

        # 3. Mapeia o que já está na planilha
        state.etapa = "lendo planilha atual"
        log.info("[Sync] Lendo planilha atual...")
        mapa_atual = _mapear_base_tpl()
        log.info(f"[Sync] {len(mapa_atual)} pedidos já na planilha.")

        # 4. Processa incremental
        state.etapa = "sincronizando Base TPL"
        auth = tpl.autenticar()

        novas_linhas   = []
        update_linhas  = {}  # linha_idx → nova linha

        for i, item in enumerate(lista_tpl):
            state.atual = i + 1
            id_tpl    = item["id"]
            order_num = str(item["order"]).strip()
            existente = mapa_atual.get(order_num)

            # Pula status finais
            if existente and any(f in existente["status"] for f in STATUS_FINAIS):
                continue

            time.sleep(0.45)
            try:
                linha = tpl.montar_linha_tpl(id_tpl, order_num, auth)
            except Exception as e:
                log.warning(f"[Sync] Erro pedido {order_num}: {e}")
                state.erros += 1
                continue

            if not linha:
                state.erros += 1
                continue

            if existente:
                update_linhas[existente["linha_idx"]] = linha
            else:
                novas_linhas.append(linha)

        # 5. Aplica updates em lote (lê dados atuais, substitui linhas modificadas)
        if update_linhas:
            state.etapa = "atualizando linhas existentes"
            log.info(f"[Sync] Atualizando {len(update_linhas)} linhas existentes...")
            todos = sheets.ler_aba("Base TPL")
            for idx, nova_linha in update_linhas.items():
                if idx < len(todos):
                    todos[idx] = nova_linha
            sheets.escrever_aba("Base TPL", todos, "A1")

        # 6. Append de novas linhas
        if novas_linhas:
            state.etapa = "gravando novos pedidos"
            log.info(f"[Sync] Gravando {len(novas_linhas)} novos pedidos...")
            sheets.append_aba("Base TPL", novas_linhas)

        # 7. Base Omie (incremental)
        _sync_omie()

        # 8. Pedidos Site (incremental)
        _sync_site()

        # 9. Recalcula Status
        _recalcular_status()

        state.fim    = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
        state.etapa  = "concluído"
        state.rodando = False
        log.info(f"[Sync] Concluído. Novos: {len(novas_linhas)} | Updates: {len(update_linhas)} | Erros: {state.erros}")
        return {
            "ok": True,
            "novos": len(novas_linhas),
            "atualizados": len(update_linhas),
            "erros": state.erros,
            "inicio": state.inicio,
            "fim": state.fim
        }

    except Exception as e:
        state.rodando = False
        state.etapa   = f"ERRO: {e}"
        log.error(f"[Sync] Erro fatal: {e}", exc_info=True)
        return {"ok": False, "msg": str(e)}


# ── Base Omie incremental ────────────────────────────────────
def _sync_omie():
    state.etapa = "sincronizando Base Omie"
    log.info("[Sync] Base Omie...")

    # Lê order codes já presentes
    dados_atual = sheets.ler_aba("Base Omie")
    ja_tem = set()
    COL_CODE_OMIE = 5  # coluna F (0-based)
    for row in dados_atual[1:]:
        if len(row) > COL_CODE_OMIE:
            ja_tem.add(str(row[COL_CODE_OMIE]).strip())

    pedidos = omie.listar_pedidos("30/07/2026")
    novas   = []
    for ped in pedidos:
        code = ped["order_code"] or "N/D"
        if code in ja_tem:
            continue
        novas.append([
            ped["data"], ped["nf"],
            "Pedido de Venda",
            "Autorizado" if ped["etapa"] == "60" else ped["etapa"],
            "API", code
        ])

    if novas:
        sheets.append_aba("Base Omie", novas)
    log.info(f"[Sync] Base Omie: +{len(novas)} novos.")


# ── Pedidos Site incremental ──────────────────────────────────
def _sync_site():
    state.etapa = "sincronizando Pedidos Site"
    log.info("[Sync] Pedidos Site...")

    dados_atual = sheets.ler_aba("Pedidos Site")
    ja_tem = set()
    COL_NUM_SITE = 1  # coluna B (0-based)
    for row in dados_atual[1:]:
        if len(row) > COL_NUM_SITE:
            ja_tem.add(str(row[COL_NUM_SITE]).strip())

    pedidos = omie.listar_pedidos("30/07/2026")
    hoje    = datetime.now(TZ_SP)
    novas   = []

    for ped in pedidos:
        code = ped["order_code"]
        if not code or code in ja_tem:
            continue

        # Dados Vnda
        time.sleep(0.25)
        vd = vnda.extrair_dados(code)

        # Dias desde o pedido
        dias = ""
        if ped["data"]:
            try:
                p = ped["data"].split("/")
                d = datetime(int(p[2]), int(p[1]), int(p[0]), tzinfo=TZ_SP)
                dias = (hoje - d).days
            except Exception:
                pass

        novas.append([
            ped["data"], code, ped["nf"],
            vd["cidade"], vd["estado"], vd["receita"],
            vd["transp"] or ped["transp"],
            dias, "NÃO",
            "INTEGRADO WMS" if ped["rastreio"] else "",
            "", "", "",
            ped["rastreio"]
        ])
        time.sleep(0.3)

    if novas:
        sheets.append_aba("Pedidos Site", novas)
    log.info(f"[Sync] Pedidos Site: +{len(novas)} novos.")


# ── Recalcula aba Status ──────────────────────────────────────
def _recalcular_status():
    state.etapa = "recalculando Status"
    log.info("[Sync] Recalculando Status...")

    dados = sheets.ler_aba("Base TPL")
    mapa  = {}
    for row in dados[1:]:
        sit = str(row[COL_SITUAC]).upper().strip() if len(row) > COL_SITUAC else ""
        val = 0.0
        if len(row) > COL_VALOR:
            try: val = float(str(row[COL_VALOR]).replace(",", "."))
            except Exception: pass
        if sit:
            if sit not in mapa:
                mapa[sit] = {"qtd": 0, "val": 0.0}
            mapa[sit]["qtd"] += 1
            mapa[sit]["val"] += val

    rows = [["Status TPL", "Qtd Pedidos", "Receita Total"]]
    rows += sorted(
        [[s, v["qtd"], round(v["val"], 2)] for s, v in mapa.items()],
        key=lambda x: -x[1]
    )

    sheets.limpar_aba("Status")
    sheets.escrever_aba("Status", rows, "A1")
    log.info(f"[Sync] Status: {len(rows)-1} situações.")
