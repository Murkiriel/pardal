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
| `datakit/build.py` | Orquestra: fontes → mescla → tiles → pacote por UF → catálogo → Brasil → formatos |
| `datakit/sources/` | Uma fonte por arquivo (OSM, DNIT, ANTT, placas ANTT, Inmetro, SNV, DERs, Rio, BH, Detran-DF, capitais) |
| `datakit/common/model.py` | Formato das linhas e a mescla: oficial ganha do OSM no mesmo ponto; limite oficial tira o do OSM a < 50 m (placa de um sentido só tira o do OSM só quando há placa dos dois sentidos) |
| `datakit/common/lrs.py` | Quilometragem das BRs pelas rotas do SNV (DNIT) e de linhas com km só nas pontas (malha da Goinfra) |
| `datakit/common/sentido.py` | Sentido dos radares e das placas: "crescente/decrescente" (ANTT, DNIT, DER-GO) vira rumo pela geometria com km; o sentido nominal de SP (Norte/Sul/Leste/Oeste) vira rumo pela via do OSM mais próxima |
| `datakit/inmetro_status.py` | Radar ativo/inativo pela validade da aferição no Inmetro, nas BRs fora de concessão |
| `datakit/build_formats.py` | Radares em GeoJSON, KML e GPX a partir dos CSVs |
| `scripts/publicar.py` | Confere falhas e quedas, organiza `data/dist/` nas pastas do repositório e reescreve o `catalog.json` |
| `datakit/falhas.py` | Registro das fontes que falharam na execução |

Cada download tenta 3 vezes quando a falha é passageira (conexão caiu, 5xx). Fonte que falha
mesmo assim (portal fora do ar, mudança de formato) é pulada e o resto sai normalmente; o build
lista as falhas no fim do log e no campo `falhas` do `data/dist/catalog.json`.

O `publicar.py` não monta os arquivos se:
- alguma fonte falhou no build (`--aceitar-falhas` para montar assim mesmo), ou
- algum estado perdeu mais de 5% (e pelo menos 50 itens) de radares, limites ou estruturas em
  relação ao `catalog.json` já publicado (`--aceitar-queda`).

## Mais

- `FONTES.md` — investigação das fontes: o que entrou, o que foi medido e descartado.
- `PENDENCIAS.md` — o que ainda falta e o que destrava cada item.
- Licença do código: MIT (`LICENSE`). Os dados gerados seguem a ODbL 1.0 (ver README da raiz).
