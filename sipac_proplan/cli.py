"""Linha de comando.

Exemplos:
  python -m sipac_proplan coletar processos.txt
  python -m sipac_proplan analisar
  python -m sipac_proplan executar processos.xlsx
  python -m sipac_proplan processo 23074.012345/2024-56
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .analise import analisar_processo, consolidar
from .cliente import ErroConsulta, SipacClient
from .config import carregar_config
from .parser import ErroParser, parse_processo
from .relatorio import gerar_excel, gerar_html, resumo_texto
from .util import extrair_numeros, ler_numeros_de_arquivos, parse_data, parse_numero

RAIZ = Path(__file__).resolve().parent.parent
PASTA_HTML = Path("dados/html")
PASTA_DEBUG = Path("dados/debug")
PASTA_SAIDA = Path("relatorios")


def _cliente(args) -> SipacClient:
    cfg_path = Path(args.config_sipac)
    dados = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    return SipacClient(
        base_url=dados.get("base_url", "https://sipac.ufpb.br"),
        atraso=float(dados.get("atraso_entre_requisicoes", 1.0)),
        campos=dados.get("consulta") or {},
        pasta_debug=PASTA_DEBUG if getattr(args, "debug", False) else None,
    )


def _numeros(args):
    numeros = ler_numeros_de_arquivos(args.entrada) if args.entrada else []
    for n in args.numero or []:
        numeros.extend(extrair_numeros(n))
    return list(dict.fromkeys(numeros))


def cmd_coletar(args) -> int:
    numeros = _numeros(args)
    ids = args.id or []
    if not numeros and not ids:
        print("Nenhum número de processo encontrado na entrada.", file=sys.stderr)
        return 2
    PASTA_HTML.mkdir(parents=True, exist_ok=True)
    cli = _cliente(args)
    ok = falhas = pulados = 0
    total = len(numeros) + len(ids)
    for i, num in enumerate(numeros, 1):
        destino = PASTA_HTML / f"{num.chave}.html"
        if destino.exists() and not args.atualizar:
            pulados += 1
            continue
        try:
            html = cli.buscar(num)
            parse_processo(html, str(num))  # valida antes de gravar
            destino.write_text(html, encoding="utf-8")
            ok += 1
            print(f"[{i}/{total}] {num} ok")
        except (ErroConsulta, ErroParser) as e:
            falhas += 1
            print(f"[{i}/{total}] {num} FALHOU: {e}", file=sys.stderr)
    for j, id_ in enumerate(ids, len(numeros) + 1):
        try:
            html = cli.detalhe_por_id(id_)
            proc = parse_processo(html, f"id={id_}")
            n = parse_numero(proc.numero)
            (PASTA_HTML / f"{n.chave}.html").write_text(html, encoding="utf-8")
            ok += 1
            print(f"[{j}/{total}] id={id_} -> {proc.numero} ok")
        except (ErroConsulta, ErroParser) as e:
            falhas += 1
            print(f"[{j}/{total}] id={id_} FALHOU: {e}", file=sys.stderr)
    print(f"\nColetados: {ok} | já em cache: {pulados} | falhas: {falhas} | pasta: {PASTA_HTML}")
    return 0 if falhas == 0 else 1


def _arquivos_html(caminhos: list[str]) -> list[Path]:
    arquivos: list[Path] = []
    for c in caminhos:
        p = Path(c)
        if p.is_dir():
            arquivos.extend(sorted(p.glob("*.htm*")))
        elif p.exists():
            arquivos.append(p)
    return arquivos


def cmd_analisar(args) -> int:
    cfg = carregar_config(args.config)
    ref = parse_data(args.referencia) if args.referencia else datetime.now()
    arquivos = _arquivos_html(args.html or [str(PASTA_HTML)])
    if not arquivos:
        print("Nenhum HTML de processo encontrado. Rode 'coletar' primeiro.", file=sys.stderr)
        return 2
    analises, erros, vistos = [], [], set()
    for arq in arquivos:
        try:
            proc = parse_processo(arq.read_text(encoding="utf-8", errors="replace"), arq.name)
        except ErroParser as e:
            erros.append(f"{arq.name}: {e}")
            continue
        if proc.numero in vistos:
            continue
        vistos.add(proc.numero)
        a = analisar_processo(proc, cfg, ref)
        if args.todos or a.passou_pela_proplan:
            analises.append(a)
    c = consolidar(analises, cfg)
    c.referencia = ref
    saida = Path(args.saida)
    base = f"painel_proplan_{ref:%Y%m%d}"
    h = gerar_html(c, cfg, saida / f"{base}.html", erros)
    x = gerar_excel(c, cfg, saida / f"{base}.xlsx")
    print(f"Processos lidos: {len(vistos)} | analisados: {len(analises)} | "
          f"na PROPLAN hoje: {sum(a.esta_na_proplan for a in analises)} | erros: {len(erros)}")
    for e in erros:
        print("  erro:", e, file=sys.stderr)
    print(f"Painel:   {h}\nPlanilha: {x}")
    return 0


def cmd_executar(args) -> int:
    rc = cmd_coletar(args)
    if rc == 2:
        return rc
    args.html = [str(PASTA_HTML)]
    return cmd_analisar(args)


def cmd_processo(args) -> int:
    cfg = carregar_config(args.config)
    num = parse_numero(args.numero)
    if not num:
        print("Número de processo inválido.", file=sys.stderr)
        return 2
    cache = PASTA_HTML / f"{num.chave}.html"
    if cache.exists() and not args.atualizar:
        html = cache.read_text(encoding="utf-8")
    else:
        try:
            html = _cliente(args).buscar(num)
        except ErroConsulta as e:
            print(e, file=sys.stderr)
            return 1
        PASTA_HTML.mkdir(parents=True, exist_ok=True)
        cache.write_text(html, encoding="utf-8")
    a = analisar_processo(parse_processo(html, str(num)), cfg)
    print(resumo_texto(a))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sipac_proplan",
        description="Consulta processos no SIPAC/UFPB e analisa a tramitação nas unidades da PROPLAN.",
    )
    ap.add_argument("--config", default=str(RAIZ / "config" / "setores.json"),
                    help="arquivo de setores/alertas (padrão: config/setores.json)")
    ap.add_argument("--config-sipac", default=str(RAIZ / "config" / "sipac.json"),
                    help="arquivo de conexão com o SIPAC (padrão: config/sipac.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def opc_coleta(p):
        p.add_argument("entrada", nargs="*",
                       help="arquivos (.txt, .csv, .xlsx, .html) contendo números de processo")
        p.add_argument("-n", "--numero", action="append", help="número de processo (pode repetir)")
        p.add_argument("--id", action="append", help="id interno do SIPAC (processo_detalhado.jsf?id=)")
        p.add_argument("--atualizar", action="store_true", help="baixa de novo mesmo se já estiver em cache")
        p.add_argument("--debug", action="store_true", help="grava as páginas intermediárias em dados/debug")

    def opc_analise(p):
        p.add_argument("--saida", default=str(PASTA_SAIDA), help="pasta dos relatórios")
        p.add_argument("--referencia", help="data de referência dd/mm/aaaa (padrão: agora)")
        p.add_argument("--todos", action="store_true",
                       help="inclui processos que nunca passaram pela PROPLAN")

    p = sub.add_parser("coletar", help="baixa do SIPAC as páginas dos processos")
    opc_coleta(p)
    p.set_defaults(func=cmd_coletar)

    p = sub.add_parser("analisar", help="gera painel HTML e planilha a partir das páginas baixadas")
    p.add_argument("html", nargs="*", help="arquivos ou pastas com HTML (padrão: dados/html)")
    opc_analise(p)
    p.set_defaults(func=cmd_analisar)

    p = sub.add_parser("executar", help="coletar + analisar")
    opc_coleta(p)
    opc_analise(p)
    p.set_defaults(func=cmd_executar)

    p = sub.add_parser("processo", help="mostra no terminal a análise de um processo")
    p.add_argument("numero")
    p.add_argument("--atualizar", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_processo)

    args = ap.parse_args(argv)
    return args.func(args)
