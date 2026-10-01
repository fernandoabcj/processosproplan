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


# ---------------------------------------------------------------- rotina agendada

_CAMPOS_VOLATEIS = {"atualizado_em", "atualizado_por", "fonte"}
_ENCERRADO = ("ARQUIV", "CONCLU", "FINALIZ", "ENCERRAD", "CANCELAD")


def _ler_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("data", d) if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _essencial(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _CAMPOS_VOLATEIS}


def planejar(db: Path, modo: str, incluir_encerrados: bool) -> tuple[list[NumeroProcesso], dict]:
    """Decide quais processos consultar, a partir de uma cópia do banco do artifact
    (pastas geradas pelo ArtifactData com out_dir: acompanhamento/lista.json,
    solicitacoes/*.json e processos/*.json)."""
    from .util import extrair_numeros

    existentes = {p.stem: d for p in (db / "processos").glob("*.json") if (d := _ler_json(p))}
    solicit = {p.stem: d for p in (db / "solicitacoes").glob("*.json") if (d := _ler_json(p))}
    numeros: dict[str, NumeroProcesso] = {}
    for d in solicit.values():
        for n in extrair_numeros(str(d.get("numero", ""))):
            numeros[n.chave] = n
    pulados = 0
    if modo == "agendado":
        lista = _ler_json(db / "acompanhamento" / "lista.json") or {}
        for txt in lista.get("numeros", []):
            for n in extrair_numeros(str(txt)):
                doc = existentes.get(n.chave)
                status = str((doc or {}).get("status", "")).upper()
                if doc and not incluir_encerrados and any(s in status for s in _ENCERRADO) and n.chave not in solicit:
                    pulados += 1
                    continue
                numeros.setdefault(n.chave, n)
    return list(numeros.values()), {"existentes": existentes, "solicitacoes": solicit, "pulados_encerrados": pulados}


def rotina(db: Path, modo: str, incluir_encerrados: bool, cliente: SipacClient, saida: Path) -> dict:
    """Consulta o SIPAC e separa em saida/alterados apenas os documentos que mudaram,
    para a gravação no artifact ser pequena. Gera saida/plano.json com o que gravar."""
    numeros, ctx = planejar(db, modo, incluir_encerrados)
    st = sincronizar(numeros, cliente, saida)
    (saida / "alterados").mkdir(parents=True, exist_ok=True)
    for velho in (saida / "alterados").glob("*.json"):
        velho.unlink()
    alterados, novos = [], []
    falhou = {f["numero"] for f in st["falhas"]}
    for arq in sorted((saida / "processos").glob("*.json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        antigo = ctx["existentes"].get(arq.stem)
        if antigo is None or _essencial(antigo) != _essencial(doc):
            (saida / "alterados" / arq.name).write_text(arq.read_text(encoding="utf-8"), encoding="utf-8")
            (novos if antigo is None else alterados).append(arq.stem)
    resolvidas = [c for c, d in ctx["solicitacoes"].items() if str(d.get("numero", "")) not in falhou
                  and (saida / "processos" / f"{c}.json").exists()]
    st.update({"modo": modo, "verificados": st["atualizados"], "alterados": len(alterados), "novos": len(novos),
               "pulados_encerrados": ctx["pulados_encerrados"]})
    (saida / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    plano = {"gravar_novos": novos, "gravar_alterados": alterados, "apagar_solicitacoes": resolvidas,
             "status": str(saida / "status.json")}
    (saida / "plano.json").write_text(json.dumps(plano, ensure_ascii=False, indent=1), encoding="utf-8")
    return plano
