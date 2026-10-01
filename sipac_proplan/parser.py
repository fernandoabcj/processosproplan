"""Leitura da página pública de detalhes do processo do SIPAC
(public/jsp/processos/processo_detalhado.jsf).

O parser é guiado pelos títulos das colunas e dos campos, e não por posições
fixas no HTML, para tolerar pequenas variações de layout entre versões do SIPAC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from .util import limpar, normalizar, parse_data, parse_numero


@dataclass
class Movimentacao:
    ordem: int
    data_envio: datetime | None
    unidade_origem: str
    unidade_destino: str
    enviado_por: str = ""
    data_recebimento: datetime | None = None
    recebido_por: str = ""
    urgente: bool = False


@dataclass
class Processo:
    numero: str
    assunto: str = ""
    assunto_detalhado: str = ""
    natureza: str = ""
    status: str = ""
    data_autuacao: datetime | None = None
    unidade_origem: str = ""
    interessados: list[str] = field(default_factory=list)
    movimentacoes: list[Movimentacao] = field(default_factory=list)
    campos: dict[str, str] = field(default_factory=dict)
    fonte: str = ""


class ErroParser(Exception):
    pass


# Palavras-chave (normalizadas) para reconhecer cada coluna da tabela de movimentações.
def _classificar_coluna(titulo: str) -> str | None:
    t = normalizar(titulo)
    if "URGENT" in t:
        return "urgente"
    if "RECEB" in t:
        if "POR" in t or "USUARIO" in t or "RECEBEDOR" in t:
            return "recebido_por"
        return "data_recebimento"
    if "ENVIADO POR" in t or "REMETENTE" in t or ("USUARIO" in t and "RECEB" not in t):
        return "enviado_por"
    if "DESTINO" in t:
        return "data_destino" if t.startswith("DATA") else "unidade_destino"
    if "ORIGEM" in t:
        return "data_envio" if t.startswith("DATA") else "unidade_origem"
    if t.startswith("DATA") or "ENVIO" in t:
        return "data_envio"
    return None


def _texto_celulas(linha: Tag) -> list[str]:
    return [limpar(c.get_text(" ")) for c in linha.find_all(["td", "th"], recursive=False)]


def _tabela_movimentacoes(soup: BeautifulSoup) -> tuple[Tag, dict[str, int], int] | None:
    """Localiza a tabela cujo cabeçalho tem colunas de origem e destino."""
    for tabela in soup.find_all("table"):
        for i, linha in enumerate(tabela.find_all("tr")):
            celulas = _texto_celulas(linha)
            if len(celulas) < 3:
                continue
            mapa: dict[str, int] = {}
            for idx, titulo in enumerate(celulas):
                tipo = _classificar_coluna(titulo)
                if tipo and tipo not in mapa:
                    mapa[tipo] = idx
            if "unidade_origem" in mapa and "unidade_destino" in mapa:
                # Ignora tabelas externas que apenas contêm a tabela real aninhada.
                if linha.find("table"):
                    continue
                return tabela, mapa, i
    return None


def _ler_movimentacoes(soup: BeautifulSoup) -> list[Movimentacao]:
    achado = _tabela_movimentacoes(soup)
    if not achado:
        return []
    tabela, mapa, idx_cab = achado
    movs: list[Movimentacao] = []
    linhas = tabela.find_all("tr")[idx_cab + 1:]
    n_min = max(mapa.values()) + 1

    def pega(celulas: list[str], chave: str) -> str:
        i = mapa.get(chave)
        return celulas[i] if i is not None and i < len(celulas) else ""

    for linha in linhas:
        celulas = _texto_celulas(linha)
        if len(celulas) < n_min:
            continue  # linhas de despacho/observação ou rodapé
        origem = pega(celulas, "unidade_origem")
        destino = pega(celulas, "unidade_destino")
        data_envio = parse_data(pega(celulas, "data_envio"))
        # Alguns layouts põem a data dentro da mesma célula da unidade.
        if data_envio is None:
            data_envio = parse_data(origem)
        if not origem and not destino:
            continue
        if data_envio is None:
            continue
        data_receb = parse_data(pega(celulas, "data_recebimento")) or parse_data(
            pega(celulas, "data_destino")
        )
        if data_receb is None and "data_recebimento" not in mapa:
            data_receb = parse_data(destino)
        urg = normalizar(pega(celulas, "urgente"))
        movs.append(
            Movimentacao(
                ordem=len(movs),
                data_envio=data_envio,
                unidade_origem=_tirar_data(origem),
                unidade_destino=_tirar_data(destino),
                enviado_por=pega(celulas, "enviado_por"),
                data_recebimento=data_receb,
                recebido_por=pega(celulas, "recebido_por"),
                urgente=urg in {"SIM", "S", "X", "TRUE"},
            )
        )
    return _ordenar(movs)


def _tirar_data(texto: str) -> str:
    import re

    return limpar(re.sub(r"\d{2}/\d{2}/\d{4}(\s+\d{1,2}:\d{2}(:\d{2})?)?", "", texto))


def _ordenar(movs: list[Movimentacao]) -> list[Movimentacao]:
    """Garante ordem cronológica crescente (o SIPAC pode listar da mais recente
    para a mais antiga). Empates mantêm a ordem relativa original."""
    if len(movs) > 1:
        datas = [m.data_envio for m in movs]
        if datas[0] > datas[-1]:
            movs = list(reversed(movs))
        movs = sorted(movs, key=lambda m: m.data_envio)
    for i, m in enumerate(movs):
        m.ordem = i
    return movs


def _ler_campos(soup: BeautifulSoup) -> dict[str, str]:
    """Coleta pares 'Rótulo: valor' (th/td ou td/td) do cabeçalho do processo."""
    campos: dict[str, str] = {}
    for linha in soup.find_all("tr"):
        celulas = linha.find_all(["th", "td"], recursive=False)
        for i in range(len(celulas) - 1):
            rotulo = limpar(celulas[i].get_text(" "))
            if not rotulo.endswith(":") or len(rotulo) > 60:
                continue
            valor = limpar(celulas[i + 1].get_text(" "))
            chave = normalizar(rotulo.rstrip(":"))
            campos.setdefault(chave, valor)
    return campos


def _campo(campos: dict[str, str], *chaves: str) -> str:
    for c in chaves:
        for k, v in campos.items():
            if k == c:
                return v
    for c in chaves:
        for k, v in campos.items():
            if k.startswith(c):
                return v
    return ""


def _ler_interessados(soup: BeautifulSoup) -> list[str]:
    for tabela in soup.find_all("table"):
        cab = normalizar(" ".join(_texto_celulas(tabela.find("tr")) if tabela.find("tr") else []))
        legenda = tabela.find("caption")
        titulo = normalizar(legenda.get_text(" ")) if legenda else ""
        if "INTERESSAD" in titulo or ("NOME" in cab and ("IDENTIFICADOR" in cab or "TIPO" in cab)):
            nomes = []
            for linha in tabela.find_all("tr")[1:]:
                cel = _texto_celulas(linha)
                if cel and not linha.find("table"):
                    nomes.append(" - ".join(x for x in cel if x))
            if nomes:
                return nomes
    return []


def parse_processo(html: str, fonte: str = "") -> Processo:
    soup = BeautifulSoup(html, "html.parser")
    campos = _ler_campos(soup)

    numero_txt = _campo(campos, "PROCESSO", "NUMERO DO PROCESSO", "NUMERO")
    num = parse_numero(numero_txt) or parse_numero(soup.get_text(" "))
    if not num:
        raise ErroParser(
            "Número do processo não encontrado na página "
            f"({fonte or 'html'}). Verifique se é a página de detalhes do processo."
        )

    proc = Processo(
        numero=str(num),
        assunto=_campo(campos, "ASSUNTO"),
        assunto_detalhado=_campo(campos, "ASSUNTO DETALHADO"),
        natureza=_campo(campos, "NATUREZA", "NATUREZA DO PROCESSO"),
        status=_campo(campos, "STATUS", "SITUACAO"),
        data_autuacao=parse_data(_campo(campos, "DATA DE AUTUACAO", "AUTUADO EM", "DATA DE CADASTRO")),
        unidade_origem=_campo(campos, "UNIDADE DE ORIGEM", "UNIDADE ORIGEM"),
        interessados=_ler_interessados(soup),
        movimentacoes=_ler_movimentacoes(soup),
        campos=campos,
        fonte=fonte,
    )
    # "Assunto" pode ter casado "Assunto Detalhado" se o primeiro não existir.
    if proc.assunto == proc.assunto_detalhado and "ASSUNTO" not in campos:
        proc.assunto = ""
    return proc
