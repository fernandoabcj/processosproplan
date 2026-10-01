"""Cliente HTTP para a consulta pública de processos do SIPAC/UFPB.

O portal público é uma aplicação JSF: cada formulário carrega um
'javax.faces.ViewState' que precisa ser devolvido no POST. Por isso o cliente
sempre abre a página de consulta, lê o formulário, preenche os campos e envia.

Os nomes exatos dos campos variam entre versões do SIPAC. O cliente tenta
reconhecê-los automaticamente; se não conseguir, é possível informá-los no
arquivo de configuração (seção "consulta" em config/sipac.json) — veja o README.
Toda página recebida pode ser gravada (opção --debug) para diagnóstico.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .util import NumeroProcesso, normalizar

BASE_URL = "https://sipac.ufpb.br"
URL_CONSULTA = "/public/jsp/processos/processo_consulta.jsf"
URL_DETALHE = "/public/jsp/processos/processo_detalhado.jsf"

_RE_ID_DETALHE = re.compile(r"processo_detalhado\.jsf\?(?:[^\"'\s]*&)?id=(\d+)")
_RE_JSFCLJS = re.compile(r"jsfcljs\(\s*document\.(?:getElementById\('([^']+)'\)|forms\['([^']+)'\])\s*,\s*\{([^}]*)\}")
_RE_PARAM_JS = re.compile(r"'([^']+)'\s*:\s*'([^']*)'")


class ErroConsulta(Exception):
    pass


def _tem_movimentacoes(html: str) -> bool:
    t = normalizar(BeautifulSoup(html, "html.parser").get_text(" "))
    return "MOVIMENTAC" in t and "DESTINO" in t and "ORIGEM" in t


class SipacClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        atraso: float = 1.0,
        timeout: float = 30.0,
        campos: dict | None = None,
        pasta_debug: Path | None = None,
        verificar_ssl: bool | str = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.atraso = atraso
        self.timeout = timeout
        self.campos = campos or {}
        self.pasta_debug = pasta_debug
        self.s = requests.Session()
        self.s.verify = verificar_ssl
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (consulta-proplan; uso institucional UFPB)",
                "Accept-Language": "pt-BR,pt;q=0.9",
            }
        )
        self._ultimo = 0.0

    # ------------------------------------------------------------ http
    def _pausa(self):
        espera = self.atraso - (time.monotonic() - self._ultimo)
        if espera > 0:
            time.sleep(espera)
        self._ultimo = time.monotonic()

    def _req(self, metodo: str, url: str, **kw) -> requests.Response:
        url = urljoin(self.base_url + "/", url.lstrip("/")) if not url.startswith("http") else url
        ultimo_erro = None
        for tentativa in range(4):
            self._pausa()
            try:
                r = self.s.request(metodo, url, timeout=self.timeout, **kw)
                r.raise_for_status()
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
            except requests.RequestException as e:
                ultimo_erro = e
                time.sleep(2 ** tentativa)
        raise ErroConsulta(f"Falha ao acessar {url}: {ultimo_erro}")

    def _debug(self, nome: str, html: str):
        if self.pasta_debug:
            self.pasta_debug.mkdir(parents=True, exist_ok=True)
            (self.pasta_debug / nome).write_text(html, encoding="utf-8")

    # ------------------------------------------------------------ API
    def detalhe_por_id(self, id_processo: str | int) -> str:
        r = self._req("GET", f"{URL_DETALHE}?id={id_processo}")
        return r.text

    def buscar(self, numero: NumeroProcesso) -> str:
        """Consulta o processo pelo número e devolve o HTML da página de detalhes."""
        r = self._req("GET", URL_CONSULTA)
        self._debug(f"{numero.chave}_1_formulario.html", r.text)
        form = self._achar_formulario(r.text)
        acao = urljoin(r.url, form.get("action") or URL_CONSULTA)
        dados = self._preencher(form, numero)

        r2 = self._req("POST", acao, data=dados)
        self._debug(f"{numero.chave}_2_resultado.html", r2.text)
        if _tem_movimentacoes(r2.text) and numero.numero in r2.text:
            return r2.text
        return self._abrir_resultado(r2, numero)

    # ------------------------------------------------------------ formulário
    def _achar_formulario(self, html: str) -> Tag:
        soup = BeautifulSoup(html, "html.parser")
        nome = self.campos.get("formulario")
        if nome:
            f = soup.find("form", id=nome) or soup.find("form", attrs={"name": nome})
            if f:
                return f
        candidatos = []
        for f in soup.find_all("form"):
            textos = [i for i in f.find_all("input") if (i.get("type") or "text").lower() == "text"]
            if textos:
                candidatos.append((len(textos), f))
        if not candidatos:
            raise ErroConsulta("Formulário de consulta não encontrado na página do SIPAC.")
        return max(candidatos, key=lambda x: x[0])[1]

    def _preencher(self, form: Tag, num: NumeroProcesso) -> dict[str, str]:
        dados: dict[str, str] = {}
        textos: list[Tag] = []
        for inp in form.find_all(["input", "select", "textarea"]):
            nome = inp.get("name")
            if not nome:
                continue
            tipo = (inp.get("type") or "text").lower()
            if inp.name == "select":
                opt = inp.find("option", selected=True) or inp.find("option")
                dados[nome] = opt.get("value", "") if opt else ""
            elif tipo in ("hidden",):
                dados[nome] = inp.get("value", "")
            elif tipo in ("text", "number", "search", "tel"):
                textos.append(inp)
                dados[nome] = inp.get("value", "")
            elif tipo in ("checkbox", "radio") and inp.has_attr("checked"):
                dados[nome] = inp.get("value", "on")

        # 1) Campos informados explicitamente na configuração
        mapa_cfg = {
            "radical": num.radical, "numero": num.numero, "ano": num.ano,
            "dv": num.dv, "completo": str(num),
        }
        explicitos = False
        for chave, valor in mapa_cfg.items():
            campo = self.campos.get(f"campo_{chave}")
            if campo:
                dados[campo] = valor
                explicitos = True
        for campo, valor in (self.campos.get("extras") or {}).items():
            dados[campo] = valor

        if not explicitos:
            self._preencher_heuristica(form, textos, dados, num)

        # Marca o critério "número do processo" (checkbox/radio associado)
        crit = self.campos.get("criterio_numero")
        if crit:
            nome, _, valor = crit.partition("=")
            dados[nome] = valor or "on"
        else:
            for inp in form.find_all("input"):
                tipo = (inp.get("type") or "").lower()
                if tipo not in ("checkbox", "radio") or not inp.get("name"):
                    continue
                rotulo = self._rotulo(form, inp)
                ident = normalizar(f"{inp.get('id', '')} {inp.get('name', '')} {rotulo}")
                if ("NUM" in ident or "PROC" in ident) and "DOC" not in ident:
                    dados[inp["name"]] = inp.get("value", "on")
                    break

        # Botão de envio
        botao = self.campos.get("botao")
        if botao:
            nome, _, valor = botao.partition("=")
            dados[nome] = valor or "Consultar"
        else:
            for b in form.find_all(["input", "button"]):
                if (b.get("type") or "").lower() not in ("submit", "image", "button", ""):
                    continue
                txt = normalizar(b.get("value") or b.get_text(" ") or "")
                if b.get("name") and ("CONSULT" in txt or "BUSCAR" in txt or "PESQUIS" in txt):
                    dados[b["name"]] = b.get("value", "")
                    break
        return dados

    @staticmethod
    def _rotulo(form: Tag, inp: Tag) -> str:
        if inp.get("id"):
            lab = form.find("label", attrs={"for": inp["id"]})
            if lab:
                return lab.get_text(" ")
        pai = inp.find_parent(["td", "tr", "label"])
        return pai.get_text(" ") if pai else ""

    def _preencher_heuristica(self, form, textos, dados, num: NumeroProcesso):
        partes_ok = 0
        for inp in textos:
            ident = normalizar(f"{inp.get('id', '')} {inp.get('name', '')}")
            ml = int(inp.get("maxlength") or 0)
            rot = normalizar(self._rotulo(form, inp))
            if "DOC" in ident or "INTERESS" in ident or "ASSUNTO" in ident:
                continue
            if "RADICAL" in ident or ml == 5:
                dados[inp["name"]] = num.radical
                partes_ok += 1
            elif "ANO" in ident or ml == 4:
                dados[inp["name"]] = num.ano
                partes_ok += 1
            elif re.search(r"\bDV\b|DIGITO|_DV|DV_", ident) or ml == 2:
                dados[inp["name"]] = num.dv
                partes_ok += 1
            elif "NUM" in ident or ml == 6:
                dados[inp["name"]] = num.numero
                partes_ok += 1
            elif ml >= 17 or ("PROCESSO" in rot and "NUM" in rot and partes_ok == 0):
                dados[inp["name"]] = str(num)
                partes_ok += 4
        if partes_ok == 0:
            raise ErroConsulta(
                "Não foi possível identificar os campos do número do processo no formulário. "
                "Rode com --debug e informe os nomes dos campos em config/sipac.json."
            )

    # ------------------------------------------------------------ resultado
    def _abrir_resultado(self, resp: requests.Response, num: NumeroProcesso) -> str:
        html = resp.text
        m = _RE_ID_DETALHE.search(html)
        if m:
            return self.detalhe_por_id(m.group(1))

        soup = BeautifulSoup(html, "html.parser")
        # Links JSF (commandLink) na linha do processo procurado
        for a in soup.find_all("a", onclick=True):
            linha = a.find_parent("tr")
            if linha is not None and num.numero not in linha.get_text():
                continue
            mm = _RE_JSFCLJS.search(a["onclick"])
            if not mm:
                continue
            form_id = mm.group(1) or mm.group(2)
            form = soup.find("form", id=form_id) or soup.find("form", attrs={"name": form_id})
            if form is None:
                continue
            dados = {
                i["name"]: i.get("value", "")
                for i in form.find_all("input")
                if i.get("name") and (i.get("type") or "").lower() == "hidden"
            }
            dados.update(dict(_RE_PARAM_JS.findall(mm.group(3))))
            r = self._req("POST", urljoin(resp.url, form.get("action") or ""), data=dados)
            self._debug(f"{num.chave}_3_detalhe.html", r.text)
            if _tem_movimentacoes(r.text):
                return r.text
            m2 = _RE_ID_DETALHE.search(r.text)
            if m2:
                return self.detalhe_por_id(m2.group(1))

        texto = normalizar(soup.get_text(" "))
        if "NENHUM" in texto and "ENCONTRAD" in texto:
            raise ErroConsulta(f"Processo {num} não encontrado no SIPAC.")
        raise ErroConsulta(
            f"Não foi possível abrir os detalhes do processo {num}. "
            "Rode com --debug e verifique os HTML gravados."
        )


def carregar_campos(caminho: Path | None) -> dict:
    if caminho and caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8")).get("consulta", {})
    return {}
