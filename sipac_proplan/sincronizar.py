"""Sincronização com a ferramenta web (Artifact "Tramitação PROPLAN").

Busca cada processo no SIPAC e grava um JSON por processo no mesmo formato de
documento que a página lê na coleção "processos", além de um status.json com o
resumo da execução (gravado no documento "status/coletor").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .cliente import ErroConsulta, SipacClient
from .parser import ErroParser, Processo, parse_processo
from .util import NumeroProcesso, parse_numero


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else None


def processo_para_doc(proc: Processo) -> dict:
    num = parse_numero(proc.numero)
    return {
        "numero": proc.numero,
        "chave": num.chave,
        "assunto": proc.assunto,
        "assunto_detalhado": proc.assunto_detalhado,
        "natureza": proc.natureza,
        "status": proc.status,
        "autuacao": _iso(proc.data_autuacao),
        "origem": proc.unidade_origem,
        "interessados": proc.interessados[:10],
        "movs": [
            {
                "envio": _iso(m.data_envio),
                "origem": m.unidade_origem,
                "destino": m.unidade_destino,
                "enviado_por": m.enviado_por,
                "receb": _iso(m.data_recebimento),
                "recebido_por": m.recebido_por,
                "urgente": m.urgente,
            }
            for m in proc.movimentacoes
        ],
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "atualizado_por": None,
        "fonte": "automatico",
    }


def sincronizar(numeros: list[NumeroProcesso], cliente: SipacClient, saida: Path,
                pasta_html: Path | None = None) -> dict:
    (saida / "processos").mkdir(parents=True, exist_ok=True)
    ok, falhas = [], []
    for num in numeros:
        try:
            html = cliente.buscar(num)
            proc = parse_processo(html, str(num))
            if not proc.movimentacoes:
                raise ErroParser("página lida, mas sem movimentações")
            if pasta_html:
                pasta_html.mkdir(parents=True, exist_ok=True)
                (pasta_html / f"{num.chave}.html").write_text(html, encoding="utf-8")
            doc = processo_para_doc(proc)
            (saida / "processos" / f"{num.chave}.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            ok.append(str(num))
        except (ErroConsulta, ErroParser) as e:
            falhas.append({"numero": str(num), "erro": str(e)[:300]})
    status = {
        "ultima_execucao": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "total": len(numeros),
        "atualizados": len(ok),
        "falhas": falhas,
        "mensagem": "" if not falhas else f"{len(falhas)} processo(s) não puderam ser lidos",
    }
    (saida / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return status
