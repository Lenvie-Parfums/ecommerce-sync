"""
sync.py — motor de sincronização INCREMENTAL
- Base TPL: update seletivo (preserva colunas manuais)
- Base Omie: incremental (só novos)
- Pedidos Site: preenche só colunas não-fórmula
- Status: não toca (fórmulas)
Estado persistido em disco — retoma se o processo reiniciar.
"""
import os, time, logging, json
from datetime import datetime
from zoneinfo import ZoneInfo

import tpl, omie, vnda, sheets

log = logging.getLogger(__name__)

DATA_INICIO   = "30/7/2026"
TZ_SP         = ZoneInfo("America/Sao_Paulo")
LOTE_GRAVACAO = 20
STATE_FILE    = "/tmp/sync_state.json"

# ── Cabeçalhos ────────────────────────────────────────────────
CAB_TPL = [
    "ID","TIPO","INTEGRACAO","NATUREZA_DE_OPERACAO","PEDIDO","ANEXO","OS","PRIORIDADE",
    "NF","VALOR_NOTA","SERIE","CHAVE","NF_EMISSAO","TRANSPORTADORA","MODALIDADE",
    "VOL.NF","PESO (G)","VOL.OP","CANAL VENDA","MKP NOME","URL","SRO",
    "CODIGO DE ROTA","ID-MKP","PREVISAO ORIGINAL","PREVISAO AJUSTADA",
    "SITUACAO","DETALHE","TEM_OCORRENCIA","ULTIMA_OCORRENCIA_NO_PEDIDO",
    "DEST_NOME","DEST_EMAIL","DEST_FONE","DEST_CEP","UF","REGIAO","GRANDE REGIAO",
    "DH/INC","DH/WMS","DH/NOTA","DH/PICKING","DH/CHECKOUT","DH/DESPACHADO",
    "DH/COLETA","DH/FALHA","DIAS_ULTIMA_MOVIMENTACAO","DH/ULTIMA_MOVIMENTACAO",
    "DH/ENTREGA"
]
CAB_OMIE = [
    "Data de Emissão (completa)","Nota Fiscal","Operação","Situação","Vendedor",
    "Código de Integração - Pedido"
]

# Índices (0-based) das colunas que o script ATUALIZA na Base TPL
# Exclui: 3(NATUREZA), 7(PRIORIDADE), 18(CANAL VENDA), 19(MKP NOME),
#         22(CODIGO ROTA), 35(REGIAO), 36(GRANDE REGIAO)
COLS_API = [
    0,1,2,4,5,6,
    8,9,10,11,12,
    13,14,15,16,17,
    20,21,23,24,25,
    26,27,28,29,
    30,31,32,33,34,
    37,38,39,40,41,42,43,44,45,46,47
]


STATUS_FINAIS = {"ENTREGUE","CANCELADO","DEVOLVIDO","EXTRAVIADO","RECUSADO"}

COL_PEDIDO = CAB_TPL.index("PEDIDO")    # 4
COL_SITUAC = CAB_TPL.index("SITUACAO")  # 26
COL_VALOR  = CAB_TPL.index("VALOR_NOTA")# 9


# ── Estado persistido ─────────────────────────────────────────
class SyncState:
    def __init__(self):
        self.rodando     = False
        self.inicio      = ""
        self.etapa       = ""
        self.total       = 0
        self.atual       = 0
        self.novos       = 0
        self.atualizados = 0
        self.erros       = 0
        self.fim         = ""
        self.idx_tpl     = 0
        self.ids_tpl     = []
        self.fase        = ""
        self._carregar()

    def _carregar(self):
        try:
            if os.path.exists(STATE_FILE):
                d = json.load(open(STATE_FILE))
                self.__dict__.update(d)
                if self.rodando:
                    log.info(f"[State] Retomando: fase={self.fase} idx={self.idx_tpl}/{self.total}")
        except Exception as e:
            log.warning(f"[State] Erro ao carregar: {e}")

    def salvar(self):
        try:
            json.dump(
                {k: v for k, v in self.__dict__.items() if not k.startswith("_")},
                open(STATE_FILE, "w")
            )
        except Exception as e:
            log.warning(f"[State] Erro ao salvar: {e}")

    def reset(self):
        self.rodando     = True
        self.inicio      = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
        self.etapa       = ""
        self.total       = 0
        self.atual       = 0
        self.novos       = 0
        self.atualizados = 0
        self.erros       = 0
        self.fim         = ""
        self.idx_tpl     = 0
        self.ids_tpl     = []
        self.fase        = "tpl"
        self.salvar()

    def to_dict(self):
        return {
            "rodando":     self.rodando,
            "etapa":       self.etapa,
            "total":       self.total,
            "atual":       self.atual,
            "pct":         round((self.atual / self.total * 100), 1) if self.total else 0,
            "novos":       self.novos,
            "atualizados": self.atualizados,
            "erros":       self.erros,
            "inicio":      self.inicio,
            "fim":         self.fim,
            "fase":        self.fase,
        }

state = SyncState()


# ── Helpers ──────────────────────────────────────────────────
def _mapear_base_tpl() -> dict:
    """Retorna {numero_pedido: {linha_idx, status, row}} — inclui row pra preservar manuais."""
    dados = sheets.ler_aba("Base TPL")
    if len(dados) <= 1:
        return {}
    mapa = {}
    for i, row in enumerate(dados[1:], start=1):
        num = str(row[COL_PEDIDO]).strip() if len(row) > COL_PEDIDO else ""
        sit = str(row[COL_SITUAC]).upper().strip() if len(row) > COL_SITUAC else ""
        if num:
            mapa[num] = {"linha_idx": i, "status": sit, "row": row}
    return mapa


def _merge_row(row_atual: list, nova_linha: list) -> list:
    """
    Mescla linha atual com nova — preserva colunas manuais,
    atualiza só as colunas da API.
    """
    total_cols = max(len(CAB_TPL), len(row_atual), len(nova_linha))
    merged = list(row_atual) + [""] * (total_cols - len(row_atual))
    for idx in COLS_API:
        if idx < len(nova_linha):
            merged[idx] = nova_linha[idx]
    return merged


def _gravar_updates(updates: dict, todos: list):
    """Aplica updates nas linhas e grava a planilha inteira."""
    for idx, nova_linha in updates.items():
        if idx < len(todos):
            todos[idx] = _merge_row(todos[idx], nova_linha)
    sheets.escrever_aba("Base TPL", todos, "A1")
    log.info(f"[Sync] {len(updates)} linhas atualizadas.")


def _gravar_novas(novas: list):
    """Faz append das novas linhas."""
    if novas:
        sheets.append_aba("Base TPL", novas)
        log.info(f"[Sync] +{len(novas)} novas linhas gravadas.")


# ── Entry point ───────────────────────────────────────────────
def rodar_sync(force: bool = False) -> dict:
    if state.rodando and not force:
        if state.fase and state.fase not in ("", "done"):
            log.info("[Sync] Retomando sync interrompido...")
            return _executar()
        return {"ok": False, "msg": "Já está rodando."}

    state.reset()

    try:
        state.etapa = "garantindo abas"
        state.salvar()
        sheets.garantir_aba("Base TPL",  CAB_TPL)
        sheets.garantir_aba("Base Omie", CAB_OMIE)

        state.etapa = "listando pedidos TPL"
        state.salvar()
        log.info("[Sync] Listando pedidos TPL...")
        lista_tpl     = tpl.listar_pedidos(DATA_INICIO)
        state.total   = len(lista_tpl)
        state.ids_tpl = [{"id": item["id"], "order": str(item["order"])} for item in lista_tpl]
        state.idx_tpl = 0
        state.fase    = "tpl"
        state.salvar()
        log.info(f"[Sync] {state.total} pedidos na lista TPL.")

        return _executar()

    except Exception as e:
        state.rodando = False
        state.etapa   = f"ERRO: {e}"
        state.salvar()
        log.error(f"[Sync] Erro fatal: {e}", exc_info=True)
        return {"ok": False, "msg": str(e)}


def _executar() -> dict:
    try:
        if state.fase == "tpl":    _processar_tpl()
        if state.fase == "omie":   _sync_omie()
        if state.fase == "site":   _sync_site()

        state.fim     = datetime.now(TZ_SP).strftime("%d/%m/%Y %H:%M:%S")
        state.etapa   = "concluído"
        state.fase    = "done"
        state.rodando = False
        state.salvar()
        log.info(f"[Sync] Concluído. Novos={state.novos} | Updates={state.atualizados} | Erros={state.erros}")
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
        state.salvar()
        log.error(f"[Sync] Erro em _executar: {e}", exc_info=True)
        return {"ok": False, "msg": str(e)}


# ── Fase TPL ─────────────────────────────────────────────────
def _processar_tpl():
    state.etapa = "sincronizando Base TPL"
    state.salvar()

    auth       = tpl.autenticar()
    mapa_atual = _mapear_base_tpl()
    log.info(f"[Sync] {len(mapa_atual)} na planilha. Retomando do índice {state.idx_tpl}.")

    novas_lote   = []
    updates_lote = {}

    # Lê planilha inteira uma vez pra fazer updates
    todos = sheets.ler_aba("Base TPL")

    for i in range(state.idx_tpl, len(state.ids_tpl)):
        item      = state.ids_tpl[i]
        id_tpl    = item["id"]
        order_num = str(item["order"]).strip()
        existente = mapa_atual.get(order_num)
        state.atual = i + 1

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
            # Update seletivo — preserva colunas manuais
            updates_lote[existente["linha_idx"]] = linha
            state.atualizados += 1
        else:
            novas_lote.append(linha)
            state.novos += 1

        # Grava lote
        if (len(novas_lote) + len(updates_lote)) >= LOTE_GRAVACAO:
            state.etapa   = f"gravando lote ({state.atual}/{state.total})"
            state.idx_tpl = i + 1
            state.salvar()

            if updates_lote:
                _gravar_updates(updates_lote, todos)
                todos = sheets.ler_aba("Base TPL")  # reatualiza após write
            if novas_lote:
                _gravar_novas(novas_lote)
                todos = sheets.ler_aba("Base TPL")

            novas_lote   = []
            updates_lote = {}
            mapa_atual   = _mapear_base_tpl()
            state.etapa  = "sincronizando Base TPL"

    # Grava sobra
    if novas_lote or updates_lote:
        state.etapa = "gravando lote final TPL"
        state.salvar()
        if updates_lote:
            _gravar_updates(updates_lote, todos)
        if novas_lote:
            _gravar_novas(novas_lote)

    state.fase    = "omie"
    state.idx_tpl = len(state.ids_tpl)
    state.salvar()
    log.info("[Sync] Base TPL concluída.")


# ── Fase Omie ─────────────────────────────────────────────────
def _sync_omie():
    state.etapa = "sincronizando Base Omie"
    state.salvar()
    log.info("[Sync] Base Omie...")

    dados_atual = sheets.ler_aba("Base Omie")
    ja_tem      = set()
    for row in dados_atual[1:]:
        if len(row) > 5:
            ja_tem.add(str(row[5]).strip())

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
            "Enviado via API", code
        ])
        if len(novas) >= 50:
            sheets.append_aba("Base Omie", novas)
            novas = []

    if novas:
        sheets.append_aba("Base Omie", novas)

    state.fase = "site"
    state.salvar()
    log.info("[Sync] Base Omie concluída.")


# ── Fase Site ─────────────────────────────────────────────────
def _sync_site():
    """
    Preenche só colunas sem fórmula na aba Pedidos Site:
    A(0)=Data, B(1)=Nº pedido, D(3)=Cidade, E(4)=Estado,
    F(5)=Receita, G(6)=Transportadora, P(15)=Rastreio TPL
    Não toca em colunas com fórmula (C,H,I,J,K,L,M,N,O).
    """
    state.etapa = "sincronizando Pedidos Site"
    state.salvar()
    log.info("[Sync] Pedidos Site...")

    # Lê pedidos já na aba (chave = Nº pedido, col B = índice 1)
    dados_site = sheets.ler_aba("Pedidos Site")
    ja_tem     = set()
    for row in dados_site[1:]:
        if len(row) > 1 and row[1]:
            ja_tem.add(str(row[1]).strip())

    pedidos = omie.listar_pedidos("30/07/2026")
    hoje    = datetime.now(TZ_SP)
    novas   = []

    for ped in pedidos:
        code = ped["order_code"]
        if not code or code in ja_tem:
            continue

        time.sleep(0.25)
        vd = vnda.extrair_dados(code)

        # Monta linha com 16 colunas (A até P)
        # Colunas com fórmula recebem "" — o Sheets já tem a fórmula lá
        linha = [""] * 7
        linha[0]  = ped["data"]                          # A: Data do Pedido
        linha[1]  = code                                 # B: Nº pedido
        linha[2]  = ped["nf"]                            # C: Nota Fiscal
        linha[3]  = vd["cidade"]                         # D: Cidade
        linha[4]  = vd["estado"]                         # E: Estado
        linha[5]  = vd["receita"]                        # F: Receita
        linha[6]  = vd["transp"] or ped["transp"]        # G: Transportadora
        # H(7): Dias — fórmula
        # I(8): Contato — fórmula
        # J(9): Status TPL — fórmula
        # K(10): Previsão — fórmula
        # L(11): Ajustada — fórmula
        # M(12): Entrega — fórmula
        # N(13): ? — fórmula
        # O(14): ? — fórmula
        # P(15): Rastreio TPL — fórmula, não toca

        novas.append(linha)
        time.sleep(0.3)

        if len(novas) >= 20:
            sheets.append_aba("Pedidos Site", novas)
            novas = []

    if novas:
        sheets.append_aba("Pedidos Site", novas)

    state.fase = "done"
    state.salvar()
    log.info("[Sync] Pedidos Site concluído.")