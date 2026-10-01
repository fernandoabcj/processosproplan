"""Carrega a configuração de setores da PROPLAN e regras de alerta."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .util import normalizar

CONFIG_PADRAO = Path(__file__).resolve().parent.parent / "config" / "setores.json"

_RE_CODIGO_UNIDADE = re.compile(r"\((\d{2}(?:\.\d{2})+)\)")


@dataclass
class Setor:
    sigla: str
    nome: str
    codigos: list[str] = field(default_factory=list)
    padroes: list[re.Pattern] = field(default_factory=list)

    def casa(self, unidade: str) -> bool:
        cod = codigo_unidade(unidade)
        if cod:
            for c in self.codigos:
                if c == cod or (c.endswith(".*") and (cod == c[:-2] or cod.startswith(c[:-1]))):
                    return True
        alvo = normalizar(unidade)
        return any(p.search(alvo) for p in self.padroes)


@dataclass
class Config:
    setores: list[Setor]
    dias_parado_no_setor: int = 15
    dias_aguardando_recebimento: int = 3
    passagens_repetidas: int = 3
    feriados: set[date] = field(default_factory=set)

    def setor_de(self, unidade: str | None) -> str | None:
        """Sigla do setor PROPLAN correspondente à unidade, ou None se for externa."""
        if not unidade:
            return None
        for s in self.setores:
            if s.casa(unidade):
                return s.sigla
        return None

    @property
    def siglas(self) -> list[str]:
        return [s.sigla for s in self.setores]

    def nome_setor(self, sigla: str) -> str:
        for s in self.setores:
            if s.sigla == sigla:
                return s.nome
        return sigla


def codigo_unidade(unidade: str) -> str | None:
    """Extrai o código SIPAC da unidade, ex.: 'COORD. DE ORÇAMENTO (11.01.04.02)'."""
    m = _RE_CODIGO_UNIDADE.search(unidade or "")
    return m.group(1) if m else None


def carregar_config(caminho: str | Path | None = None) -> Config:
    p = Path(caminho) if caminho else CONFIG_PADRAO
    dados = json.loads(p.read_text(encoding="utf-8"))
    setores = [
        Setor(
            sigla=s["sigla"],
            nome=s.get("nome", s["sigla"]),
            codigos=[c.strip() for c in s.get("codigos", [])],
            padroes=[re.compile(rx) for rx in s.get("padroes", [])],
        )
        for s in dados["setores"]
    ]
    alertas = dados.get("alertas", {})
    feriados = {date.fromisoformat(f) for f in dados.get("feriados", [])}
    return Config(
        setores=setores,
        dias_parado_no_setor=int(alertas.get("dias_parado_no_setor", 15)),
        dias_aguardando_recebimento=int(alertas.get("dias_aguardando_recebimento", 3)),
        passagens_repetidas=int(alertas.get("passagens_repetidas", 3)),
        feriados=feriados,
    )
