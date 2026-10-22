# Search-Law-Works-Brazil

Bot de pesquisa que compila um relatorio objetivo e completo sobre o trabalho no
Brasil em 2026: legislacao (CLT), modalidades de contratacao (CLT, PJ, MEI,
informal, plataformas), previdencia, tributos e mudancas em tramitacao no
Congresso — com indicacao de quem e prejudicado ou beneficiado por cada mudanca.

## Arquitetura

```
src/
├── cli.py          Interface terminal interativa (chat RAG + comandos)
├── web.py          Interface web local (FastAPI) com chat e botao de relatorio
├── collectors/     Coleta de fontes: Planalto, Camara, Senado, IBGE, artigos, noticias
├── chunking.py     Divisao dos documentos em fragmentos indexaveis
├── rag.py          Busca BM25 sobre o corpus (com boost de fonte oficial)
├── llm.py          Cliente Ollama (cloud com rotacao de chaves + fallback local)
├── report.py       Relatorio secao a secao com citacao ABNT obrigatoria
├── charts.py       Graficos matplotlib e diagramas com legenda ABNT
├── calculos.py     Parametros oficiais de 2026 (INSS, IRRF, FGTS, 13o, ferias)
├── insights.py     Series deterministicas: informal x formal e poder de compra
├── latex.py        Exportacao MD -> PDF via Pandoc + Tectonic (padrao)
├── pdf.py          Fallback MD -> HTML -> PDF (Edge/Chrome headless)
└── config.py       Carregamento de YAMLs e .env
config/
├── sources.yaml    Fontes de coleta
└── outline.yaml    Sumario e briefing de cada secao do relatorio
data/               Cache bruto e corpus indexado
output/             Relatorios .md, .html e .pdf
```

## Requisitos

- Python 3.12+
- Ollama instalado (modelo local `llama3.1:8b` opcional como fallback)
- [Pandoc](https://pandoc.org/installing.html) + [Tectonic](https://tectonic-typesetting.github.io)
  para o PDF via LaTeX (recomendado); sem eles o bot usa Edge/Chrome como fallback

## Instalacao

```bash
pip install -r requirements.txt
cp .env.example .env   # se existir; senao crie o .env com as chaves abaixo
```

Variaveis do `.env`:

```
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_CLOUD_URL=https://ollama.com
OLLAMA_CLOUD_MODEL=gemma4:31b-cloud
OLLAMA_LOCAL_MODEL=llama3.1:8b
OLLAMA_API_KEYS=chave1,chave2,chave3
```

As chaves do Ollama cloud sao rotacionadas automaticamente quando uma atinge o
limite de uso; se todas falharem, o bot usa o modelo local.

## Uso

### Terminal (interativo)

```bash
python -m src            # chat interativo (padrao)
```

Comandos do chat:

| Comando | Funcao |
|---|---|
| `/coletar` | coleta as fontes e reconstroi o corpus |
| `/relatorio` | gera o relatorio completo em MD e PDF |
| `/relatorio clt,mei` | gera apenas as secoes indicadas |
| `/auditar` | audita o ultimo relatorio (citacoes, graficos, frases) |
| `/buscar <texto>` | busca fragmentos sem usar o LLM |
| `/status` | estado do corpus e da configuracao |
| `/ajuda` | lista de comandos |
| `/sair` | encerra |

### Web local

```bash
python -m src web        # sobe em http://127.0.0.1:8000
```

A pagina oferece chat com busca no corpus e botoes para coletar fontes,
gerar o relatorio e baixar o `.md`.

### Execucao direta

```bash
python -m src coletar                    # so coleta
python -m src relatorio                  # relatorio completo
python -m src relatorio --so-secoes clt,mei
python -m src relatorio --refresh        # ignora cache
python -m src auditar                    # audita o ultimo relatorio
python -m src status
```

## Relatorio

O sumario (15 secoes) fica em `config/outline.yaml` e cobre: panorama PNAD,
principios constitucionais, CLT e reforma de 2017, vinculo de emprego e
pejotizacao, modalidades (CLT, temporario, intermitente, remoto, autonomo,
MEI, PJ, cooperado, informal, plataformas), comparacao PJ x CLT, MEI e
informal, **informal x formal pelos dados oficiais**, **cesta basica e poder
de compra nas cinco regioes**, previdencia e reforma tributaria, plataformas
digitais (PL 12/2024, Convencao OIT 193/2026), tramitacao 2026 no Congresso,
riscos e checklist, quadro-resumo comparativo.

Saida fixa em `output/relatorio-trabalho-brasil-2026-10-16.{md,html,pdf}`:
reexecucoes sobrescrevem o mesmo trio de arquivos (sem arquivo datado novo).

Padrao do relatorio:

- Cada afirmacao cita uma fonte em formato ABNT hibrido com link clicavel,
  ex.: `(BRASIL, 1943, art. 58)`; referencias finais na NBR 6023.
- Bloco "**Em termos simples:**" ao fim de cada secao, em linguagem de leigo.
- Minimo de 1 grafico por secao (matplotlib com legenda "Figura N - ... Fonte:"),
  diagramas comparativos/fluxo em HTML para as modalidades.
- Secoes 5 e 6 detalham Direitos, Deveres, base legal, atuacao do MTE e leitura
  simplificada de cada modalidade de trabalho, com tabela de direitos x deveres.
- Geracao com validacao mecanica (retries + reparo) e auditoria final via
  `auditar` (citacoes linkadas, frases proibidas, graficos, capa/resumo).

### Secoes 1, 9 e 10: numeros deterministicos

As secoes **"Informal x formal"** e **"Cesta basica e poder de compra nas cinco
Grandes Regioes"** nao dependem do LLM para os numeros: o modulo
`src/insights.py` le as series oficiais, aplica a metodologia de calculo
(`src/calculos.py`: INSS progressivo de 2026, IRRF com o redutor da Lei
15.270/2025, FGTS de 8%, 13o, ferias + 1/3) e injeta no markdown:

- tabelas com os numeros exatos (rendimento por categoria de emprego em 2012 e
  no ultimo ano da serie; teste do argumento informal x formal; cesta, salario
  necessario e renda das cinco regioes);
- 4 graficos por secao (rendimento e composicao por categoria; folha do
  empregado; equivalente anual formal x informal; cesta e salario necessario
  por regiao; renda real x necessario; percentual do salario minimo).

A secao **"Panorama"** tambem recebe dois graficos deterministicos (nivel de
ocupacao por grupo de idade e composicao da populacao por condicao e grupo de
idade, ambos da tabela SIDRA 4094), inseridos quando o modelo nao produziu um
equivalente.

O texto do LLM usa esses numeros como fonte e os gráficos entram com nota de
metodologia e ressalvas de comparabilidade.


## Fontes coletadas

- Planalto: CLT, Constituicao, FGTS, Lei 6.019, Lei 13.467, LC 123 (MEI),
  Lei 8.212 (INSS), Lei 8.213, Lei 12.288, Lei 14.611
- Legislacao complementar: EC 103/2019, EC 132/2023, LC 214/2025, Codigo Civil
- Artigos academicos (SciELO/OpenAlex/Crossref)
- API de proposicoes da Camara (palavras-chave de trabalho)
- API de materias do Senado
- IBGE (PNAD Continua via API de agregados: desocupacao, ocupados, rendimento)
- IBGE SIDRA **tabela 4659** (rendimento medio real por categoria de emprego,
  serie anual 2012-2025) e **tabela 4097** (composicao dos ocupados por
  categoria, trimestral 2012-2026)
- Ipea SERIES: formalidade, informalidade, renda por sexo e por Grande Regiao
- DIEESE/Conab: Pesquisa Nacional da Cesta Basica (boletins mensais 2015-2026)
  e salario minimo necessario
- Receita Federal (tabelas de IRRF de 2026) e MPS/INSS (tabela de contribuicao
  de 2026: teto de R$ 8.475,55 e aliquotas progressivas)
- Google News RSS (URLs reais resolvidas, sem redirecionadores)

Cache em `data/cache` (use `--refresh` para forcar recoleta); corpus em
`data/corpus/*.jsonl`. As pastas `data/` e `output/` sao gerados localmente
(cache de coleta e relatorios) e ficam fora do versionamento via `.gitignore`.

## Aviso

Documento de carater informativo gerado automaticamente a partir de fontes
publicas. Nao substitui orientacao juridica profissional.
