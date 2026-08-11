"""
sync.py — motor de sincronização INCREMENTAL com gravação em tempo real
Grava a cada LOTE_GRAVACAO pedidos processados — não perde progresso se cair.
"""
import time, logging
from datetime import datetime
from zoneinfo import ZoneInfo

import tpl, omie, vnda, sheets

log = logging.getLogger(__name__)

DATA_INICIO   = "30/7/2026"
TZ_SP         = ZoneInfo("America/Sao_Paulo")
LOTE_GRAVACAO = 50  # grava na planilha a cada X pedidos

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

COL_PEDIDO = CAB_TPL.index("PEDIDO")
COL_SITUAC = CAB_TPL.index("SITUACAO")
COL_VALOR  = CAB_TPL.index("VALOR_NOTA")

STATUS_FINAIS = {"ENTREGUE","CANCELADO","DEVOLVIDO","EXTRAVIADO","RECUSADO"}


# ── Estado global ─────────────────────────────────────────────
class SyncState:
    rodando:     bool = False
    inicio:      str  = ""
    etapa:       str  = ""
    total:       int  = 0
    atual:       int  = 0
    novos:       int  = 0
    atualizados: int  = 0
    erros:       int  = 0
    fim:         str  = ""

state = SyncState()


# ── Helpers ──────────────────────────────────────────────────
def _mapear_base_tpl() -> dict:
    dados = sheets.ler_aba("Base TPL")
    if len(dados) <= 1:
        return {}
    mapa = {}
    for i, row in enumerate(dados[1:], start=1):
        num = str(row[COL_PEDIDO]).strip() if len(row) > COL_PEDIDO else ""
        sit = str(row[COL_SITUAC]).upper().strip() if len(row) > COL_SITUAC else ""
        if num:
            mapa[num] = {"linha_idx": i, "status": sit}
    return mapa


def _gravar_lote(novas: list, updates: dict):
    """Grava novas linhas e updates na planilha imediatamente."""
    if updates:
        todos = sheets.ler_aba("Base TPL")
        for idx, nova_linha in updates.items():
            if idx < len(todos):
                todos[idx] = nova_linha
        sheets.escrever_aba("Base TPL", todos, "A1")

    if novas:
        sheets.append_aba("Base TPL", novas)

    log.info(f"[Sync] Lote gravado: +{len(novas)} novos, {len(updates)} atualizados.")


# ── Sincronização principal ───────────────────────────────────
def rodar_sync() -> dict:
    if state.rodando:
        return {"ok": False, "msg": "Já está rodando."}

    state.rodando    = True
    state.inicio     = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
    state.fim        = ""
    state.erros      = 0
    state.novos      = 0
    state.atualizados= 0

    try:
        # 1. Garante abas
        state.etapa = "garantindo abas"
        sheets.garantir_aba("Base TPL",     CAB_TPL)
        sheets.garantir_aba("Base Omie",    CAB_OMIE)
        sheets.garantir_aba("Pedidos Site", CAB_SITE)

        # 2. Lista pedidos TPL
        state.etapa = "listando pedidos TPL"
        log.info("[Sync] Listando pedidos TPL...")
        lista_tpl   = tpl.listar_pedidos(DATA_INICIO)
        state.total = len(lista_tpl)
        log.info(f"[Sync] {state.total} pedidos na lista TPL.")

        # 3. Mapeia planilha atual
        state.etapa = "lendo planilha atual"
        log.info("[Sync] Lendo planilha atual...")
        mapa_atual  = _mapear_base_tpl()
        log.info(f"[Sync] {len(mapa_atual)} pedidos já na planilha.")

        # 4. Processa incremental com gravação por lote
        state.etapa  = "sincronizando Base TPL"
        auth         = tpl.autenticar()
        novas_lote   = []
        updates_lote = {}

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
                updates_lote[existente["linha_idx"]] = linha
                state.atualizados += 1
            else:
                novas_lote.append(linha)
                state.novos += 1

            # Grava a cada LOTE_GRAVACAO pedidos
            if (len(novas_lote) + len(updates_lote)) >= LOTE_GRAVACAO:
                state.etapa = f"gravando lote ({state.atual}/{state.total})"
                _gravar_lote(novas_lote, updates_lote)
                novas_lote   = []
                updates_lote = {}
                mapa_atual   = _mapear_base_tpl()
                state.etapa  = "sincronizando Base TPL"

        # Grava o que sobrou
        if novas_lote or updates_lote:
            state.etapa = "gravando lote final"
            _gravar_lote(novas_lote, updates_lote)

        # 5. Base Omie
        _sync_omie()

        # 6. Pedidos Site
        _sync_site()

        # 7. Status
        _recalcular_status()

        state.fim     = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
        state.etapa   = "concluído"
        state.rodando = False
        log.info(f"[Sync] Concluído. Novos: {state.novos} | Updates: {state.atualizados} | Erros: {state.erros}")
        return {
            "ok": True,
            "novos": state.novos,
            "atualizados": state.atualizados,
            "erros": state.erros,
            "inicio": state.inicio,
            "fim": state.fim
        }

    except Exception as e:
        state.rodando = False
        state.etapa   = f"ERRO: {e}"
        log.error(f"[Sync] Erro fatal: {e}", exc_info=True)
        return {"ok": False, "msg": str(e)}


# ── Base Omie incremental ─────────────────────────────────────
def _sync_omie():
    state.etapa = "sincronizando Base Omie"
    log.info("[Sync] Base Omie...")

    dados_atual   = sheets.ler_aba("Base Omie")
    ja_tem        = set()
    COL_CODE_OMIE = 5
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
        if len(novas) >= 50:
            sheets.append_aba("Base Omie", novas)
            novas = []

    if novas:
        sheets.append_aba("Base Omie", novas)
    log.info("[Sync] Base Omie concluída.")


# ── Pedidos Site incremental ──────────────────────────────────
def _sync_site():
    state.etapa = "sincronizando Pedidos Site"
    log.info("[Sync] Pedidos Site...")

    dados_atual  = sheets.ler_aba("Pedidos Site")
    ja_tem       = set()
    COL_NUM_SITE = 1
    for row in dados_atual[1:]:
        if len(row) > COL_NUM_SITE:
            ja_tem.add(str(row[COL_NUM_SITE]).strip())

    # Mapeia as datas da Base TPL para cruzar com os Pedidos Site (sem chamar API de novo)
    dados_tpl = sheets.ler_aba("Base TPL")
    mapa_tpl = {}
    for row in dados_tpl[1:]:
        if len(row) > 4: # COL_PEDIDO = 4
            num = str(row[4]).strip()
            mapa_tpl[num] = {
                "prev":    str(row[24]) if len(row) > 24 else "",
                "ajust":   str(row[25]) if len(row) > 25 else "",
                "entrega": str(row[47]) if len(row) > 47 else ""
            }

    pedidos = omie.listar_pedidos("30/07/2026")
    hoje    = datetime.now(TZ_SP)
    novas   = []

    for ped in pedidos:
        code = ped["order_code"]
        if not code or code in ja_tem:
            continue

        time.sleep(0.25)
        vd = vnda.extrair_dados(code)

        dias = ""
        if ped["data"]:
            try:
                p    = ped["data"].split("/")
                d    = datetime(int(p[2]), int(p[1]), int(p[0]), tzinfo=TZ_SP)
                dias = (hoje - d).days
            except Exception:
                pass

        # Pega as datas da Base TPL mapeada acima
        tpl_info = mapa_tpl.get(code, {})

        novas.append([
            ped["data"], code, ped["nf"],
            vd["cidade"], vd["estado"], vd["receita"],
            vd["transp"] or ped["transp"],
            dias, "NÃO",
            "INTEGRADO WMS" if ped["rastreio"] else "",
            tpl_info.get("prev", ""),
            tpl_info.get("ajust", ""),
            tpl_info.get("entrega", ""),
            ped["rastreio"]
        ])
        time.sleep(0.3)

        if len(novas) >= 20:
            sheets.append_aba("Pedidos Site", novas)
            novas = []

    if novas:
        sheets.append_aba("Pedidos Site", novas)
    log.info("[Sync] Pedidos Site concluído.")


# ── Recalcula Status ──────────────────────────────────────────
def _recalcular_status():
    state.etapa = "recalculando Status"
    log.info("[Sync] Recalculando Status...")

    dados = sheets.ler_aba("Base TPL")
    mapa  = {}
    for row in dados[1:]:
        sit = str(row[COL_SITUAC]).upper().strip() if len(row) > COL_SITUAC else ""
        val = 0.0
        if len(row) > COL_VALOR:
            try:
                val = float(str(row[COL_VALOR]).replace(",", "."))
            except Exception:
                pass
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
    log.info(f"[Sync] Status recalculado: {len(rows)-1} situações.")
