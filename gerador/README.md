# Gerador do Pardal

Código que gera os arquivos deste repositório (`brasil/`, `estados/` e `catalog.json`) a
partir das fontes abertas listadas no README principal.

## Requisitos

- Python 3.10+ (testado com 3.12) e o binário `curl` no PATH (a PBH barra o TLS do Python, o curl passa)
- `pip install -r requirements.txt` (pyosmium, requests, openpyxl, shapely, pyshp, pypdf, nas
  versões testadas)
- ~2 GB de disco para os extratos do OpenStreetMap e cerca de 1 h de processamento

## Como rodar

Da pasta `gerador/`:

```
python run.py datakit.build --all        # baixa as fontes e gera data/dist/ (27 UFs + Brasil)
python run.py datakit.build --uf GO      # só um estado
python scripts/publicar.py               # monta brasil/, estados/ e catalog.json na raiz do repositório
python -m unittest discover -s datakit/tests -t .
```

Verificação de estilo e de tipos (ferramentas em `requirements-dev.txt`, configuração em
`ruff.toml` e `mypy.ini`):

```
pip install -r requirements-dev.txt
python -m ruff check datakit scripts run.py
python -m mypy datakit scripts
```

`build --all` já gera os radares em GeoJSON, KML e GPX; para gerá-los de novo sobre um
`data/dist/` existente: `python run.py datakit.build_formats`. Limites e estruturas saem só em
CSV; o `scripts/para_geojson.py` (na raiz do repositório) converte qualquer um para GeoJSON (testado em
`datakit/tests/test_para_geojson.py`).

Os extratos do OSM ficam em `data/raw/`. A cada build o gerador confere na Geofabrik se há
versão mais nova e só então baixa de novo; `--osm-local` usa os guardados sem conferir (útil
para repetir um build com os mesmos dados).

## Como funciona

| Módulo | Papel |
|---|---|
| `datakit/build.py` | Orquestra: fontes → mescla → UF de cada ponto → pacote por UF → catálogo → Brasil → formatos |
| `datakit/contexto.py` | O estado de uma execução (OSM da região, fontes oficiais, SNV, Inmetro, malha das UFs) e o contrato das fontes: cada fonte oficial expõe `carregar(ctx) -> Carga` (radares, limites, locais desativados) |
| `datakit/sources/` | Uma fonte por arquivo (OSM, DNIT, ANTT, placas ANTT, Inmetro, SNV, DERs, Rio, BH, Detran-DF, capitais) |
| `datakit/common/model.py` | Formato das linhas e a mescla: radar do OSM a até 30 m de um oficial compatível vira um só (o oficial); radar do OSM num local desativado pela CET fica inativo; limite oficial tira o do OSM a < 50 m (placa de um sentido só tira o do OSM só quando há placa dos dois sentidos) |
| `datakit/common/lrs.py` | Quilometragem das BRs pelas rotas do SNV (DNIT) e de linhas com km só nas pontas (malha da Goinfra) |
| `datakit/common/sentido.py` | Sentido dos radares e das placas: "crescente/decrescente" (ANTT, DNIT, DER-GO) vira rumo pela geometria com km; o sentido nominal de SP (Norte/Sul/Leste/Oeste) vira rumo pela via do OSM mais próxima |
| `datakit/inmetro_status.py` | Radar ativo/inativo pela validade da aferição no Inmetro, nas BRs fora de concessão |
| `datakit/build_formats.py` | Radares em GeoJSON, KML e GPX a partir dos CSVs |
| `scripts/publicar.py` | Confere falhas e quedas, organiza `data/dist/` nas pastas do repositório e reescreve o `catalog.json` |
| `datakit/falhas.py` | Registro das fontes que falharam na execução |

Cada download tenta 3 vezes quando a falha é passageira (conexão caiu, 5xx), com no máximo 20 s
para abrir a conexão. Um servidor que não abre conexão nem assim é dado como fora do ar pelo resto
da execução (as próximas requisições a ele falham na hora). Fonte que falha (portal fora do ar,
mudança de formato) é pulada e o resto sai normalmente; o build lista as falhas no fim do log e no
campo `falhas` do `data/dist/catalog.json`. Quando havia uma cópia anterior em `data/raw/` (Inmetro
por UF, radares da CET), ela é usada e vira **aviso** (`avisos`), que aparece no build e no
`publicar.py` mas não bloqueia a publicação.

Cada build grava `data/audit/juncoes_<UF>.csv` com todos os radares juntados (duplicatas entre
órgãos, OSM com oficial, OSM com OSM): distância, fontes, tipos, limites, sentido e situação dos
dois lados — para conferir as junções.

`python scripts/testar_fontes.py` testa, em segundos, se cada fonte responde de onde se está
rodando (útil antes de um build em máquina nova ou na nuvem).

Com `--commit`, o `publicar.py` põe no commit só `brasil/`, `estados/` e `catalog.json` —
nunca outras mudanças pendentes no repositório.

O `publicar.py` não monta os arquivos se:
- o build ainda está rodando ou caiu no meio (`data/dist/BUILD_EM_ANDAMENTO`, que o build cria
  ao começar e apaga ao terminar; sem opção para passar por cima: rode o build de novo),
- algum arquivo a publicar passa de 95 MB (o GitHub recusa acima de 100 MB; o build já registra
  isso como falha),
- alguma fonte falhou no build (`--aceitar-falhas` para montar assim mesmo), ou
- algum estado perdeu mais de 5% (e pelo menos 50 itens) de radares, limites ou estruturas em
  relação ao `catalog.json` já publicado (`--aceitar-queda`).

## Publicação automática

O workflow `.github/workflows/gerar-dados.yml` gera e publica os dados todo dia 5 (03:00 em
Brasília) numa máquina do GitHub Actions, e pode ser disparado à mão (Actions → Gerar dados →
Run workflow; só publica com "publicar" marcado). Roda os testes, o build completo e o
`publicar.py --commit --push`, com as mesmas travas: fonte que falhou, estado que perdeu mais de
5% ou arquivo acima de 95 MB fazem o job falhar sem publicar nada. O log do build, a auditoria
das junções e o catálogo ficam nos artefatos da execução por 30 dias.

As máquinas do Actions ficam nos EUA, e o DNIT e o Inmetro não aceitam conexão de fora do Brasil
(o portal da ANTT às vezes devolve uma página de bloqueio): o tráfego para esses três servidores,
e só para eles, sai por um túnel WireGuard com saída no Brasil. A configuração do túnel fica no
segredo `MULLVAD_WG_CONF` do repositório (Settings → Secrets and variables → Actions); sem ela o
job para no começo. Testado em 2026-09-30: build completo em ~30 min, mesmos números do build
local.

## Mais

- `FONTES.md` — investigação das fontes: o que entrou, o que foi medido e descartado.
- `PENDENCIAS.md` — o que ainda falta e o que destrava cada item.
- Licença do código: MIT (`LICENSE`). Os dados gerados seguem a ODbL 1.0 (ver README da raiz).
