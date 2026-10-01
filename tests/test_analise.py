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
    assert cfg.setor_de("COORDENAÇÃO DE ORÇAMENTO (11.00.20.02)") == "CODEOR"
    assert cfg.setor_de("SECRETARIA DA PRÓ-REITORIA DE PLANEJAMENTO (11.00.20.01)") == "SECRETARIA"
    assert cfg.setor_de("PROPLAN - SECRETARIA (11.00.20.01)") == "SECRETARIA"
    assert cfg.setor_de("PRÓ-REITORIA DE PLANEJAMENTO E DESENVOLVIMENTO (11.00.20)") == "PROPLAN"
    assert cfg.setor_de("COORDENAÇÃO DE CONVÊNIOS (11.00.20.04)") == "CODECON"
    assert cfg.setor_de("PROCURADORIA FEDERAL (11.00.05)") is None


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
