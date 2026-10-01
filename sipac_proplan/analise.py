"""Cálculo de permanências por unidade/setor, situação atual e indicadores.

Convenção de contagem (para cada movimentação i que envia o processo à unidade X):
  - início da permanência em X  = data de envio da movimentação i
  - recebimento em X            = data de recebimento da movimentação i (se houver)
  - fim da permanência em X     = data de envio da movimentação i+1 (próximo despacho)
  - se não houver próxima movimentação, a permanência está em aberto até a data de referência

Assim, o tempo "na caixa" aguardando recebimento é atribuído à unidade de destino e
apresentado separadamente (dias_aguardando_recebimento).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .parser import Processo
from .util import dias_corridos, dias_uteis, normalizar

_STATUS_ENCERRADO = ("ARQUIV", "CONCLU", "FINALIZ", "ENCERRAD", "CANCELAD", "APENSAD", "ANEXAD")


@dataclass
class Permanencia:
    unidade: str
    setor: str | None  # sigla do setor PROPLAN ou None (unidade externa)
    inicio: datetime
    recebimento: datetime | None
    fim: datetime | None  # None = permanência atual (em aberto)
    dias: float
    dias_uteis: int
    dias_aguardando_recebimento: float
    veio_de: str
    foi_para: str = ""

    @property
    def aberta(self) -> bool:
        return self.fim is None


@dataclass
class AnaliseProcesso:
    processo: Processo
    permanencias: list[Permanencia]
    referencia: datetime
    encerrado: bool
    unidade_atual: str = ""
    setor_atual: str | None = None
    dias_unidade_atual: float = 0.0
    dias_uteis_unidade_atual: int = 0
    aguardando_recebimento: bool = False
    dias_tramitacao_total: float = 0.0
    dias_por_setor: dict[str, float] = field(default_factory=dict)
    dias_uteis_por_setor: dict[str, int] = field(default_factory=dict)
    passagens_por_setor: dict[str, int] = field(default_factory=dict)
    dias_total_proplan: float = 0.0
    primeira_entrada_proplan: datetime | None = None
    ultima_saida_proplan: datetime | None = None
    alertas: list[str] = field(default_factory=list)

    @property
    def passou_pela_proplan(self) -> bool:
        return bool(self.passagens_por_setor)

    @property
    def esta_na_proplan(self) -> bool:
        return self.setor_atual is not None and not self.encerrado


def analisar_processo(proc: Processo, cfg: Config, referencia: datetime | None = None) -> AnaliseProcesso:
    ref = referencia or datetime.now()
    status_n = normalizar(proc.status)
    encerrado = any(s in status_n for s in _STATUS_ENCERRADO)
    movs = [m for m in proc.movimentacoes if m.data_envio]

    perms: list[Permanencia] = []

    def nova(unidade, inicio, receb, fim, veio_de, foi_para=""):
        fim_calc = fim or ref
        if receb:
            aguard = dias_corridos(inicio, receb)
        elif fim is None:
            aguard = dias_corridos(inicio, ref)  # ainda não recebido
        else:
            aguard = 0.0  # seguiu adiante sem registro de recebimento
        perms.append(
            Permanencia(
                unidade=unidade,
                setor=cfg.setor_de(unidade),
                inicio=inicio,
                recebimento=receb,
                fim=fim,
                dias=dias_corridos(inicio, fim_calc),
                dias_uteis=dias_uteis(inicio, fim_calc, cfg.feriados),
                dias_aguardando_recebimento=aguard,
                veio_de=veio_de,
                foi_para=foi_para,
            )
        )

    if movs:
        # Permanência na unidade de origem, da autuação até o primeiro envio.
        origem = proc.unidade_origem or movs[0].unidade_origem
        inicio_origem = proc.data_autuacao or movs[0].data_envio
        if inicio_origem and inicio_origem <= movs[0].data_envio:
            nova(movs[0].unidade_origem or origem, inicio_origem, inicio_origem,
                 movs[0].data_envio, "(autuação)", movs[0].unidade_destino)

        for i, m in enumerate(movs):
            prox = movs[i + 1] if i + 1 < len(movs) else None
            nova(
                m.unidade_destino,
                m.data_envio,
                m.data_recebimento,
                prox.data_envio if prox else None,
                m.unidade_origem,
                prox.unidade_destino if prox else "",
            )
    elif proc.data_autuacao:
        nova(proc.unidade_origem, proc.data_autuacao, proc.data_autuacao, None, "(autuação)")

    a = AnaliseProcesso(processo=proc, permanencias=perms, referencia=ref, encerrado=encerrado)
    if not perms:
        a.alertas.append("Sem movimentações legíveis na página")
        return a

    atual = perms[-1]
    a.unidade_atual = atual.unidade
    a.setor_atual = atual.setor
    a.dias_unidade_atual = atual.dias
    a.dias_uteis_unidade_atual = atual.dias_uteis
    a.aguardando_recebimento = atual.recebimento is None and not encerrado
    a.dias_tramitacao_total = dias_corridos(perms[0].inicio, ref)

    dias = defaultdict(float)
    uteis = defaultdict(int)
    passagens = Counter()
    for p in perms:
        if not p.setor:
            continue
        dias[p.setor] += p.dias
        uteis[p.setor] += p.dias_uteis
        passagens[p.setor] += 1
        if a.primeira_entrada_proplan is None:
            a.primeira_entrada_proplan = p.inicio
        if p.fim and p.foi_para and cfg.setor_de(p.foi_para) is None:
            a.ultima_saida_proplan = p.fim
    a.dias_por_setor = dict(dias)
    a.dias_uteis_por_setor = dict(uteis)
    a.passagens_por_setor = dict(passagens)
    a.dias_total_proplan = sum(dias.values())

    # Alertas gerenciais
    if a.esta_na_proplan:
        if a.dias_unidade_atual >= cfg.dias_parado_no_setor:
            a.alertas.append(
                f"Parado há {a.dias_unidade_atual:.0f} dias em {a.setor_atual} "
                f"(limite {cfg.dias_parado_no_setor})"
            )
        if a.aguardando_recebimento and a.dias_unidade_atual >= cfg.dias_aguardando_recebimento:
            a.alertas.append(
                f"Aguardando recebimento em {a.setor_atual} há {a.dias_unidade_atual:.0f} dias"
            )
    for setor, n in passagens.items():
        if n >= cfg.passagens_repetidas:
            a.alertas.append(f"{n} passagens por {setor} (idas e vindas)")
    for p in perms:
        if p.setor and p.fim and p.recebimento is None:
            a.alertas.append(
                f"Saiu de {p.setor} em {p.fim:%d/%m/%Y} sem registro de recebimento"
            )
            break
    return a


# ---------------------------------------------------------------- agregados


@dataclass
class EstatisticaSetor:
    sigla: str
    nome: str
    processos: int = 0
    passagens: int = 0
    dias: list[float] = field(default_factory=list)  # por passagem concluída
    aguardando_receb: list[float] = field(default_factory=list)
    em_estoque: int = 0
    estoque_dias: list[float] = field(default_factory=list)
    estoque_acima_limite: int = 0

    def _st(self, valores, func):
        return func(valores) if valores else 0.0

    @property
    def media(self) -> float:
        return self._st(self.dias, statistics.mean)

    @property
    def mediana(self) -> float:
        return self._st(self.dias, statistics.median)

    @property
    def maximo(self) -> float:
        return self._st(self.dias, max)

    @property
    def media_aguardando(self) -> float:
        return self._st(self.aguardando_receb, statistics.mean)

    @property
    def idade_media_estoque(self) -> float:
        return self._st(self.estoque_dias, statistics.mean)


@dataclass
class Consolidado:
    analises: list[AnaliseProcesso]
    setores: list[EstatisticaSetor]
    fluxos: Counter  # (origem, destino) -> quantidade (envolvendo PROPLAN)
    entradas: Counter  # unidade externa que envia para a PROPLAN
    saidas: Counter  # unidade externa que recebe da PROPLAN
    referencia: datetime


def _rotulo(unidade: str, cfg: Config) -> str:
    return cfg.setor_de(unidade) or unidade


def consolidar(analises: list[AnaliseProcesso], cfg: Config) -> Consolidado:
    stats = {s.sigla: EstatisticaSetor(s.sigla, s.nome) for s in cfg.setores}
    fluxos: Counter = Counter()
    entradas: Counter = Counter()
    saidas: Counter = Counter()

    for a in analises:
        vistos = set()
        for p in a.permanencias:
            if p.setor:
                st = stats[p.setor]
                st.passagens += 1
                vistos.add(p.setor)
                if p.fim:
                    st.dias.append(p.dias)
                if p.recebimento:
                    st.aguardando_receb.append(p.dias_aguardando_recebimento)
            origem_setor = cfg.setor_de(p.veio_de)
            if p.veio_de and p.veio_de != "(autuação)" and (p.setor or origem_setor):
                fluxos[(_rotulo(p.veio_de, cfg), p.setor or p.unidade)] += 1
                if p.setor and not origem_setor:
                    entradas[p.veio_de] += 1
                if origem_setor and not p.setor:
                    saidas[p.unidade] += 1
        for s in vistos:
            stats[s].processos += 1
        if a.esta_na_proplan:
            st = stats[a.setor_atual]
            st.em_estoque += 1
            st.estoque_dias.append(a.dias_unidade_atual)
            if a.dias_unidade_atual >= cfg.dias_parado_no_setor:
                st.estoque_acima_limite += 1

    ref = max((a.referencia for a in analises), default=datetime.now())
    return Consolidado(analises, list(stats.values()), fluxos, entradas, saidas, ref)
