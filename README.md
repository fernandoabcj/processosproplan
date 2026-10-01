# Processos PROPLAN — análise de tramitação no SIPAC/UFPB

Ferramenta para consultar processos na consulta pública do SIPAC
(<https://sipac.ufpb.br/public/jsp/portal.jsf>) e analisar a tramitação nas unidades da
PROPLAN: **Gabinete PROPLAN, Secretaria, CODEOR, CODEINFO, CODECON e CODEPLAN**.

Para cada processo, a ferramenta calcula:

- todas as movimentações, ou seja, de onde o processo veio e para onde foi, com datas de envio e recebimento;
- os **dias em cada setor**, em dias corridos e dias úteis. Quando o processo passa mais de uma vez pelo mesmo setor, as passagens são somadas;
- o tempo **aguardando recebimento**, isto é, entre o envio e o "receber" no SIPAC;
- o **destino atual**, há quantos dias o processo está lá e se já foi recebido;
- o tempo total de tramitação e o tempo total dentro da PROPLAN;
- alertas: processo parado acima do limite, aguardando recebimento, muitas idas e vindas, ou saída sem registro de recebimento.

Na visão consolidada, a ferramenta mostra:

- para cada setor: média, mediana e máximo de dias por passagem;
- o estoque atual e a idade desse estoque;
- de onde os processos chegam e para onde saem.

## Instalação

Requer Python 3.10 ou superior.

```bash
pip install -r requirements.txt
```

## Uso

1. **Liste os processos** num arquivo `.txt`, `.csv`, `.xlsx` ou mesmo `.html`. A ferramenta encontra
   sozinha números no formato `23074.012345/2026-56`, com ou sem pontuação. Uma forma prática de
   montar a lista é exportar ou copiar a relação de "Processos na Unidade" do SIPAC de cada setor.

2. **Colete e analise:**

```bash
python -m sipac_proplan executar processos.txt
```

Esse comando gera os arquivos em `relatorios/`:

- `painel_proplan_AAAAMMDD.html`: painel para abrir no navegador, com indicadores, desempenho por setor, estoque
  atual, alertas, fluxos e a movimentação detalhada de cada processo. As colunas podem ser ordenadas e há campo de filtro;
- `painel_proplan_AAAAMMDD.xlsx`: planilha com as abas Resumo por setor, Processos, Permanências,
  Movimentações e Fluxos.

Outros comandos:

```bash
python -m sipac_proplan coletar processos.txt -n 23074.000777/2026-10   # só baixa (com cache)
python -m sipac_proplan coletar --id 1234567       # pelo id de processo_detalhado.jsf?id=
python -m sipac_proplan analisar                    # reanalisa o que já foi baixado
python -m sipac_proplan analisar --referencia 30/09/2026 --todos
python -m sipac_proplan processo 23074.012345/2026-56   # resumo no terminal
python -m sipac_proplan coletar processos.txt --atualizar  # baixa de novo (dados do dia)
```

As páginas baixadas ficam em `dados/html/` e funcionam como cache. Também é possível salvar
manualmente pelo navegador a página de detalhes de um processo ("Salvar como…") e rodar
`python -m sipac_proplan analisar minha_pasta/`.

## Configuração dos setores (`config/setores.json`)

Cada setor é reconhecido pelo **código SIPAC da unidade**, por exemplo `11.00.20.02`, que é o critério mais
seguro, ou por expressões regulares aplicadas ao nome da unidade. **Recomenda-se preencher
`codigos`** com os códigos reais das unidades da PROPLAN. Sem isso, o padrão "COORDENACAO DE
PLANEJAMENTO", por exemplo, pode casar com a coordenação de planejamento de um Centro. A primeira regra
que casar define o setor, por isso a PROPLAN genérica fica por último.

Os limites dos alertas e a lista de feriados (usada no cálculo de dias úteis) também ficam nesse arquivo.

## Regra de contagem

O tempo de um processo em uma unidade vai do **envio para ela** até o **próximo envio feito por
ela**. O intervalo até o "receber" está incluído nesse tempo e também é mostrado separadamente
como "dias até receber". A primeira permanência considerada é na unidade de origem, contada da
autuação até o primeiro envio. Média, mediana e máximo por setor consideram só as passagens já
concluídas. As passagens em aberto aparecem como estoque.

## Se a consulta automática falhar

A consulta pública do SIPAC é uma aplicação JSF. O cliente lê o formulário e tenta identificar
sozinho os campos do número do processo. Se o layout do SIPAC da UFPB tiver nomes diferentes, siga estes passos:

1. Rode `python -m sipac_proplan coletar -n <numero> --debug`.
2. Abra `dados/debug/*_1_formulario.html` e veja o atributo `name` dos campos.
3. Informe esses nomes na seção `consulta` de `config/sipac.json`. Exemplo:

```json
"consulta": {
  "campo_radical": "formConsulta:radical",
  "campo_numero": "formConsulta:numero",
  "campo_ano": "formConsulta:ano",
  "campo_dv": "formConsulta:dv",
  "criterio_numero": "formConsulta:checkNumero=on",
  "botao": "formConsulta:btnConsultar=Consultar"
}
```

Também é possível usar `"campo_completo"` quando o formulário tem um único campo para o número inteiro.

## Testes

```bash
pip install pytest && python -m pytest -q
```
