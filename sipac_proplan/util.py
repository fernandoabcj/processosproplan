"""Funções auxiliares: números de processo, datas e contagem de dias."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

# Formato do número de processo do Governo Federal (NUP):
#   23074.012345/2024-56  ->  radical.numero/ano-dv
# Aceita variações de pontuação/espaços e o formato apenas com dígitos (17 dígitos).
_RE_PROCESSO = re.compile(
    r"(?<!\d)(\d{5})\s*[.\-]?\s*(\d{6})\s*/\s*(\d{4})\s*-?\s*(\d{2})(?!\d)"
)
_RE_PROCESSO_DIGITOS = re.compile(r"(?<!\d)(\d{5})(\d{6})(\d{4})(\d{2})(?!\d)")

_RE_DATA_HORA = re.compile(
    r"(\d{2})/(\d{2})/(\d{4})(?:\s+(?:às\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)


@dataclass(frozen=True, order=True)
class NumeroProcesso:
    radical: str
    numero: str
    ano: str
    dv: str

    def __str__(self) -> str:
        return f"{self.radical}.{self.numero}/{self.ano}-{self.dv}"

    @property
    def chave(self) -> str:
        """Identificador seguro para nome de arquivo."""
        return f"{self.radical}{self.numero}{self.ano}{self.dv}"


def parse_numero(texto: str) -> NumeroProcesso | None:
    m = _RE_PROCESSO.search(texto) or _RE_PROCESSO_DIGITOS.search(texto)
    if not m:
        return None
    return NumeroProcesso(*m.groups())


def extrair_numeros(texto: str) -> list[NumeroProcesso]:
    """Extrai todos os números de processo de um texto qualquer (CSV, HTML, TXT...),
    sem repetição e preservando a ordem de aparição."""
    vistos: dict[NumeroProcesso, None] = {}
    for rx in (_RE_PROCESSO, _RE_PROCESSO_DIGITOS):
        for m in rx.finditer(texto):
            vistos.setdefault(NumeroProcesso(*m.groups()), None)
    return list(vistos)


def ler_numeros_de_arquivos(caminhos: Iterable[str | Path]) -> list[NumeroProcesso]:
    vistos: dict[NumeroProcesso, None] = {}
    for caminho in caminhos:
        p = Path(caminho)
        if p.suffix.lower() in {".xlsx", ".xlsm"}:
            texto = _texto_de_planilha(p)
        else:
            texto = p.read_text(encoding="utf-8", errors="replace")
        for n in extrair_numeros(texto):
            vistos.setdefault(n, None)
    return list(vistos)


def _texto_de_planilha(p: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(p, read_only=True, data_only=True)
    partes = []
    for ws in wb.worksheets:
        for linha in ws.iter_rows(values_only=True):
            partes.extend(str(v) for v in linha if v is not None)
    return "\n".join(partes)


def parse_data(texto: str | None) -> datetime | None:
    if not texto:
        return None
    m = _RE_DATA_HORA.search(texto)
    if not m:
        return None
    d, mes, a, h, mi, s = m.groups()
    try:
        return datetime(int(a), int(mes), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None


def normalizar(texto: str) -> str:
    """Maiúsculas, sem acentos e com espaços simples (para comparação)."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def limpar(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def dias_corridos(inicio: datetime, fim: datetime) -> float:
    return max((fim - inicio).total_seconds() / 86400.0, 0.0)


def dias_uteis(inicio: datetime, fim: datetime, feriados: set[date] | None = None) -> int:
    """Quantidade de dias úteis (seg-sex, exceto feriados) decorridos entre as datas,
    sem contar o dia de início (mesma convenção de contagem de prazos)."""
    if fim <= inicio:
        return 0
    feriados = feriados or set()
    d = inicio.date() + timedelta(days=1)
    total = 0
    while d <= fim.date():
        if d.weekday() < 5 and d not in feriados:
            total += 1
        d += timedelta(days=1)
    return total
