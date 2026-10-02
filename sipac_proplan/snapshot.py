"""Fotografia pública dos dados (dados.json) publicada junto com a página.

O banco do artifact só entrega dados a quem está conectado ao claude.ai. Para que o
link público mostre os mesmos números, a página carrega este arquivo quando o banco
não está disponível. Formato compacto: os nomes das unidades ficam numa tabela e as
movimentações viram listas, o que reduz o arquivo a cerca de um terço.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

VERSAO = 1


def _ler(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d.get("data", d) if isinstance(d, dict) else None


def montar(processos: dict[str, dict], lista: dict | None, status: dict | None, config: dict | None) -> dict:
    unidades: dict[str, int] = {}

    def u(nome: str | None) -> int:
        return unidades.setdefault(nome or "", len(unidades))

    linhas = []
    for chave in sorted(processos):
        d = processos[chave]
        linhas.append([
            d.get("numero", ""), d.get("assunto", ""), d.get("assunto_detalhado", ""),
            d.get("natureza", ""), d.get("status", ""), d.get("autuacao"), u(d.get("origem")),
            d.get("interessados", []),
            [[m.get("envio"), u(m.get("origem")), u(m.get("destino")), m.get("enviado_por", ""),
              m.get("receb"), m.get("recebido_por", ""), 1 if m.get("urgente") else 0]
             for m in d.get("movs", [])],
            d.get("atualizado_em"),
        ])
    return {
        "v": VERSAO,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "unidades": list(unidades),
        "processos": linhas,
        "lista": lista or {},
        "status": status or {},
        "config": config or None,
    }


def exportar(db: Path, saida: Path, sync: Path | None = None) -> dict:
    """Lê a cópia do banco (pastas do ArtifactData com out_dir) e, se houver, aplica por
    cima os documentos alterados na última rotina (sync/alterados) e o status novo."""
    processos = {p.stem: d for p in (db / "processos").glob("*.json") if (d := _ler(p))}
    status = _ler(db / "status" / "coletor.json")
    if sync:
        for p in (sync / "alterados").glob("*.json"):
            if d := _ler(p):
                processos[p.stem] = d
        status = _ler(sync / "status.json") or status
    dados = montar(processos, _ler(db / "acompanhamento" / "lista.json"), status,
                   _ler(db / "config" / "setores.json"))
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"processos": len(processos), "bytes": saida.stat().st_size}
