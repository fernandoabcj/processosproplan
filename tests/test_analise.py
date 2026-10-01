from datetime import datetime
from pathlib import Path

import pytest

from sipac_proplan.analise import analisar_processo, consolidar
from sipac_proplan.cliente import SipacClient
from sipac_proplan.config import carregar_config
from sipac_proplan.parser import parse_processo
from sipac_proplan.relatorio import gerar_excel, gerar_html, resumo_texto
from sipac_proplan.util import dias_uteis, extrair_numeros, parse_numero

FIX = Path(__file__).parent / "fixtures"
REF = datetime(2026, 10, 1, 10, 0)


@pytest.fixture
def cfg():
    return carregar_config()


def _ler(nome):
    return parse_processo((FIX / nome).read_text(encoding="utf-8"), nome)


def test_extrai_numeros_em_varios_formatos():
    nums = extrair_numeros((FIX / "lista_numeros.txt").read_text())
    assert [str(n) for n in nums] == [
        "23074.012345/2026-56", "23074.000777/2026-10", "23074.000123/2026-11",
    ]


def test_parser_ordena_e_le_campos():
    p = _ler("processo_em_andamento.html")
    assert p.numero == "23074.012345/2026-56"
    assert p.status == "ATIVO"
    assert p.assunto.startswith("004.1")
    assert p.assunto_detalhado.startswith("SOLICITAÇÃO")
    assert p.data_autuacao == datetime(2026, 9, 2)
    assert len(p.movimentacoes) == 3  # linha de despacho ignorada
    assert p.movimentacoes[0].data_envio == datetime(2026, 9, 5, 14, 30)
    assert p.movimentacoes[0].urgente
    assert p.movimentacoes[-1].data_recebimento is None
    assert p.interessados


def test_classificacao_setores(cfg):
    assert cfg.setor_de("PROPLAN - COORDENAÇÃO DE ORÇAMENTO (11.01.07.04)") == "CODEOR"
    assert cfg.setor_de("PROPLAN - SECRETARIA (11.01.07.01)") == "SECRETARIA"
    assert cfg.setor_de("PROPLAN - SECRETARIA (11.01.07.01)") == "SECRETARIA"
    assert cfg.setor_de("PRÓ-REITORIA DE PLANEJAMENTO E DESENVOLVIMENTO (11.01.07)") == "PROPLAN"
    assert cfg.setor_de("PROPLAN - COORDENAÇÃO DE CONVÊNIOS (11.01.07.05)") == "CODECON"
    assert cfg.setor_de("PROCURADORIA FEDERAL (11.00.05)") is None
    # Unidades de outros órgãos com nome parecido não podem ser confundidas
    assert cfg.setor_de("SOF - COORDENAÇÃO DE ORÇAMENTO E FINANÇAS (11.00.46.38.01)") is None
    # Qualquer unidade abaixo de 11.01.07 é PROPLAN, mesmo sem regra específica
    assert cfg.setor_de("PROPLAN - DIVISÃO NOVA (11.00.61.09)") == "PROPLAN"
    # Códigos reais (relatórios SIPAC de 01/10/2026)
    assert cfg.setor_de("PRÓ-REITORIA DE PLANEJAMENTO (PROPLAN) (11.00.61)") == "PROPLAN"
    assert cfg.setor_de("PROPLAN - COORDENAÇÃO DE INFORMAÇÃO (11.00.61.01)") == "CODEINFO"
    assert cfg.setor_de("PROPLAN - SECRETARIA EXECUTIVA (11.00.61.02)") == "SECRETARIA"
    assert cfg.setor_de("PROPLAN - COORDENAÇÃO DE PLANEJAMENTO (11.01.07.05)") == "CODEPLAN"
    assert cfg.setor_de("PROPLAN - COORDENAÇÃO DE CONVÊNIOS (11.01.07.06)") == "CODECON"


def test_analise_processo_em_andamento(cfg):
    a = analisar_processo(_ler("processo_em_andamento.html"), cfg, REF)
    assert a.setor_atual == "CODEOR"
    assert a.esta_na_proplan and a.aguardando_recebimento
    assert a.dias_unidade_atual == pytest.approx(6.0)  # 25/09 10:00 -> 01/10 10:00
    # Secretaria: enviado 05/09 14:30, saiu 15/09 09:00
    assert a.dias_por_setor["SECRETARIA"] == pytest.approx(9.77, abs=0.01)
    assert a.dias_por_setor["PROPLAN"] == pytest.approx(10.04, abs=0.01)
    assert a.permanencias[0].unidade.startswith("CENTRO DE CIENCIAS")  # origem até 1º envio
    assert not any("Parado" in x for x in a.alertas)
    assert any("Aguardando recebimento" in x for x in a.alertas)


def test_analise_processo_arquivado_com_retorno(cfg):
    a = analisar_processo(_ler("processo_arquivado.html"), cfg, REF)
    assert a.encerrado and not a.esta_na_proplan
    assert a.passagens_por_setor == {"SECRETARIA": 1, "CODECON": 2}
    assert a.dias_por_setor["CODECON"] == pytest.approx(24 + 10)
    assert a.ultima_saida_proplan == datetime(2026, 4, 30)


def test_consolidado_e_relatorios(cfg, tmp_path):
    analises = [analisar_processo(_ler(n), cfg, REF)
                for n in ("processo_em_andamento.html", "processo_arquivado.html")]
    c = consolidar(analises, cfg)
    codecon = next(s for s in c.setores if s.sigla == "CODECON")
    assert codecon.processos == 1 and codecon.passagens == 2
    codeor = next(s for s in c.setores if s.sigla == "CODEOR")
    assert codeor.em_estoque == 1
    assert c.entradas["GABINETE DA REITORIA (11.00.01)"] == 1
    assert c.saidas["PROCURADORIA FEDERAL (11.00.05)"] == 1

    h = gerar_html(c, cfg, tmp_path / "p.html")
    assert "23074.012345/2026-56" in h.read_text(encoding="utf-8")
    x = gerar_excel(c, cfg, tmp_path / "p.xlsx")
    assert x.stat().st_size > 0
    assert "CODEOR" in resumo_texto(analises[0])


def test_dias_uteis():
    # sexta 25/09/2026 -> quinta 01/10/2026 = seg..qui = 4 dias úteis
    assert dias_uteis(datetime(2026, 9, 25), datetime(2026, 10, 1)) == 4


def test_preenchimento_formulario_heuristico():
    from bs4 import BeautifulSoup

    html = """<form id="formConsulta" action="/public/jsp/processos/processo_consulta.jsf">
      <input type="hidden" name="formConsulta" value="formConsulta">
      <input type="checkbox" name="formConsulta:checkNumProc" id="formConsulta:checkNumProc">
      <label for="formConsulta:checkNumProc">Número do Processo</label>
      <input type="text" name="formConsulta:radical" maxlength="5">
      <input type="text" name="formConsulta:numProc" maxlength="6">
      <input type="text" name="formConsulta:anoProc" maxlength="4">
      <input type="text" name="formConsulta:dvProc" maxlength="2">
      <input type="text" name="formConsulta:assunto">
      <input type="submit" name="formConsulta:btnConsultar" value="Consultar">
      <input type="hidden" name="javax.faces.ViewState" value="j_id1">
    </form>"""
    form = BeautifulSoup(html, "html.parser").form
    d = SipacClient()._preencher(form, parse_numero("23074.012345/2026-56"))
    assert d["formConsulta:radical"] == "23074"
    assert d["formConsulta:numProc"] == "012345"
    assert d["formConsulta:anoProc"] == "2026"
    assert d["formConsulta:dvProc"] == "56"
    assert d["formConsulta:checkNumProc"] == "on"
    assert d["formConsulta:btnConsultar"] == "Consultar"
    assert d["javax.faces.ViewState"] == "j_id1"


def test_sincronizar_gera_documentos(tmp_path):
    import json

    from sipac_proplan.sincronizar import sincronizar

    class Falso:
        def buscar(self, num):
            if num.numero == "012345":
                return (FIX / "processo_em_andamento.html").read_text(encoding="utf-8")
            from sipac_proplan.cliente import ErroConsulta
            raise ErroConsulta("não encontrado")

    nums = [parse_numero("23074.012345/2026-56"), parse_numero("23074.999999/2026-99")]
    st = sincronizar(nums, Falso(), tmp_path)
    assert st["atualizados"] == 1 and len(st["falhas"]) == 1
    doc = json.loads((tmp_path / "processos" / "23074012345202656.json").read_text(encoding="utf-8"))
    assert doc["chave"] == "23074012345202656"
    assert doc["movs"][0]["envio"] == "2026-09-05T14:30"
    assert doc["movs"][-1]["receb"] is None


def test_duracao_legivel():
    from sipac_proplan.util import duracao

    assert duracao(1.5) == "1 dia 12h"
    assert duracao(0.2) == "4h 48min"
    assert duracao(0.01) == "14 min"
    assert duracao(154.4) == "154 dias 9h"
    assert duracao(2) == "2 dias"


def test_rotina_grava_so_o_que_mudou(tmp_path):
    import json

    from sipac_proplan.sincronizar import processo_para_doc, rotina

    db = tmp_path / "db"
    (db / "processos").mkdir(parents=True)
    (db / "acompanhamento").mkdir()
    (db / "solicitacoes").mkdir()
    proc = _ler("processo_em_andamento.html")
    antigo = processo_para_doc(proc)
    antigo["atualizado_em"] = "2026-09-01T00:00:00Z"
    (db / "processos" / f"{antigo['chave']}.json").write_text(json.dumps(antigo), encoding="utf-8")
    arquivado = processo_para_doc(_ler("processo_arquivado.html"))
    (db / "processos" / f"{arquivado['chave']}.json").write_text(json.dumps(arquivado), encoding="utf-8")
    (db / "acompanhamento" / "lista.json").write_text(json.dumps(
        {"numeros": ["23074.012345/2026-56", "23074.000777/2026-10"]}), encoding="utf-8")
    (db / "solicitacoes" / "23074000123202611.json").write_text(
        json.dumps({"numero": "23074.000123/2026-11"}), encoding="utf-8")

    class Falso:
        def buscar(self, num):
            nome = {"012345": "processo_em_andamento.html", "000123": "processo_em_andamento.html"}[num.numero]
            html = (FIX / nome).read_text(encoding="utf-8")
            return html.replace("23074.012345/2026-56", str(num))

    plano = rotina(db, "agendado", False, Falso(), tmp_path / "sync")
    assert plano["gravar_alterados"] == []            # nada mudou no processo já existente
    assert plano["gravar_novos"] == ["23074000123202611"]
    assert plano["apagar_solicitacoes"] == ["23074000123202611"]
    st = json.loads((tmp_path / "sync" / "status.json").read_text(encoding="utf-8"))
    assert st["pulados_encerrados"] == 1              # arquivado não é consultado fora das 07h


def test_rotina_de_hora_em_hora_so_confere_o_foco(tmp_path):
    import json
    from datetime import datetime

    from sipac_proplan.sincronizar import planejar, processo_para_doc

    db = tmp_path / "db"
    (db / "processos").mkdir(parents=True)
    (db / "acompanhamento").mkdir()
    na_proplan = processo_para_doc(_ler("processo_em_andamento.html"))   # hoje na CODEOR
    fora = dict(na_proplan, numero="23074.000555/2026-11", chave="23074000555202611",
                movs=na_proplan["movs"][:1])                             # último destino: Secretaria? não
    fora["movs"] = [dict(fora["movs"][0], destino="PROCURADORIA FEDERAL (11.00.05)", envio="2026-09-01T10:00")]
    for d in (na_proplan, fora):
        (db / "processos" / f"{d['chave']}.json").write_text(json.dumps(d), encoding="utf-8")
    (db / "acompanhamento" / "lista.json").write_text(json.dumps({"numeros": [
        na_proplan["numero"], fora["numero"], "23074.000999/2026-99"]}), encoding="utf-8")
    agora = datetime(2026, 10, 1, 10, 0)
    nums, ctx = planejar(db, "agendado", False, completo=False, agora=agora)
    assert sorted(str(n) for n in nums) == ["23074.000999/2026-99", "23074.012345/2026-56"]
    assert ctx["pulados_fora_do_foco"] == 1
    nums, _ = planejar(db, "agendado", False, completo=True, agora=agora)
    assert len(nums) == 3
