# Pardal — radares e limites de velocidade do Brasil

**Lista de radares do Brasil** (fixos, lombadas eletrônicas, radares de trecho e avanço de
sinal) e **limites de velocidade das vias**, com localização (latitude e longitude), em
arquivos para baixar e usar offline: **CSV**, **GeoJSON**, **KML** (Google Earth) e **GPX**
(GPS e apps de navegação). Um conjunto do Brasil inteiro e um por estado.

"Pardal" é o apelido popular do radar fixo. O conjunto junta, num formato só, o que DNIT,
ANTT, Inmetro, DERs estaduais e prefeituras publicam em dezenas de lugares diferentes, mais
o que a comunidade do OpenStreetMap mapeia.

> **Aviso.** Não é fonte oficial e nenhum órgão endossa este trabalho. Os dados podem
> estar desatualizados ou errados. A sinalização da via sempre prevalece.

**Brasil inteiro:** [`radares.csv`](brasil/radares.csv) · [`radares.gpx`](brasil/radares.gpx) ·
[`radares.kml`](brasil/radares.kml) · [`radares.geojson`](brasil/radares.geojson) ·
[`limites.csv`](brasil/limites.csv) · [`limites_estimados.csv`](brasil/limites_estimados.csv) ·
[`estruturas.csv`](brasil/estruturas.csv)
**Por estado:** pasta [`estados/`](estados), por exemplo [`estados/SP/`](estados/SP).

## Organização

```
brasil/                 o país inteiro
  radares.csv           todos os radares
  radares.gpx           radares ativos, para GPS e apps de navegação
  radares.kml           radares ativos, para Google Earth
  radares.geojson       todos os radares, para mapas web e QGIS
  limites.csv           pontos de limite de velocidade sinalizados
  limites_estimados.csv limites estimados pelo tipo de via (.csv.gz se passar de 95 MB)
  estruturas.csv        pontes, viadutos e túneis
estados/<UF>/           um estado (AC, AL, AM … SP, TO)
  radares.csv  radares.gpx  radares.kml  radares.geojson
  limites.csv           limites sinalizados e estimados (coluna `estimated`)
  estruturas.csv
catalogo.json           índice dos pacotes por estado: arquivos, tamanho, sha256,
                        contagens e data de geração
scripts/to_geojson.py converte qualquer CSV acima para GeoJSON (ver abaixo)
gerador/                código que baixa as fontes e gera todos os arquivos acima
```

Limites e estruturas são publicados só em CSV (os limites do Brasil com os estimados num
arquivo à parte, para caber no limite de 100 MB por arquivo do GitHub). Para ter qualquer um
deles em GeoJSON, use o `scripts/to_geojson.py`.
Limites não saem em GPX nem KML: aparelhos de GPS usam esses formatos para pontos de
interesse, e o limite é um valor da via, não um lugar.

## Formato

**Radares (CSV):** `lat,lng,kind,limit_kmh,source,active,end_lat,end_lng,direction_deg`

| Coluna | Significado |
|---|---|
| `kind` | `FIXED` (radar de velocidade ou lombada eletrônica), `SECTION` (radar de trecho, com o fim em `end_lat,end_lng`), `RED_LIGHT` (avanço de sinal) |
| `limit_kmh` | Limite fiscalizado, quando a fonte informa (veículo leve) |
| `source` | De onde veio o ponto (`OSM`, `DNIT`, `ANTT`, `DER-SP`, `RIO`, `INMETRO`…). Quando mais de uma fonte traz o mesmo radar, elas vêm juntas com `+` (ex.: `DNIT+OSM`, confirmado por duas fontes independentes). `INMETRO` sozinho é um radar que só o cadastro de aferições do Inmetro conhece, com a posição tirada do endereço (ver as perguntas frequentes) |
| `active` | `0` quando o radar está desativado ou com a aferição do Inmetro vencida |
| `direction_deg` | Sentido fiscalizado: rumo do trânsito que o radar fiscaliza, em graus a partir do norte (0 = norte, 90 = leste, 180 = sul, 270 = oeste). Vazio quando o radar fiscaliza os dois sentidos ou a fonte não informa |

No GPX e no KML entram só os radares ativos, com o nome no formato "Radar 60 km/h" e o
sentido na descrição. O GeoJSON tem todos, com as mesmas informações nas propriedades.

**Limites (CSV):** `lat,lng,limit_kmh,source,estimated,limit_low_kmh,direction_deg`. Cada ponto
marca o limite naquele lugar da via; o valor vale até o próximo ponto. Onde há dado oficial
(placas das concessões federais, velocidade regulamentada das ruas do Rio de Janeiro e de São
Paulo), ele substitui o do OpenStreetMap naquele trecho.

| Coluna | Significado |
|---|---|
| `source` | `ANTT`, `RIO`, `CET-SP`, `OSM` (valor sinalizado no mapa), `OSM:zone` ou `OSM:class` (estimados) |
| `estimated` | `1` quando o valor **não é sinalizado**: foi estimado pela regra do Código de Trânsito para aquele tipo de via (rodovia, avenida, via local), porque a via não tem limite cadastrado. `0` para valor sinalizado ou oficial |
| `limit_low_kmh` | Só nos estimados: a mesma estimativa pelo lado baixo da faixa legal, para quem prefere errar para o lado cauteloso (ex.: 80 onde o típico é 100) |
| `direction_deg` | Preenchido quando a placa vale só para um sentido (rumo do trânsito em graus, como nos radares). Nas rodovias concedidas em que cada sentido tem um limite diferente, e nas vias do OpenStreetMap marcadas com um limite por sentido (`maxspeed:forward`/`maxspeed:backward`), há um ponto para cada sentido no mesmo lugar. Vazio = vale para os dois sentidos |

Os estimados cobrem só a rede principal (autoestradas, troncos, primárias, secundárias e
terciárias). Estradas rurais sem classificação e ruas de bairro ficam sem estimativa.

**Estruturas (CSV):** `lat1,lng1,lat2,lng2,kind`, com `kind` = `BRIDGE` ou `TUNNEL`.

Todos em UTF-8, coordenadas em graus decimais (WGS84/SIRGAS 2000).

## Converter para GeoJSON

O `scripts/to_geojson.py` converte qualquer CSV do Pardal (radares, limites, limites estimados,
estruturas, inclusive `.csv.gz`) para GeoJSON. Só precisa de Python 3.8 ou mais novo, sem
instalar nada:

```
python scripts/to_geojson.py estados/GO/limites.csv                   # gera estados/GO/limites.geojson
python scripts/to_geojson.py estados/GO/limites.csv --no-estimated   # só os limites sinalizados
python scripts/to_geojson.py brasil/limites_estimados.csv -o estimados.geojson
```

Pontos viram `Point`, estruturas viram `LineString` e as demais colunas viram propriedades
(`active` e `estimated` como verdadeiro/falso). Arquivos grandes são convertidos sem
carregar tudo na memória (o Brasil inteiro, 1,4 milhão de pontos, leva cerca de 10 s).

## Perguntas frequentes

**Onde baixar a lista de radares do Brasil atualizada?** Na pasta `brasil/`: `radares.csv`
(planilha), `radares.gpx` (GPS) ou `radares.kml` (Google Earth). Para um estado só, use a
pasta dele, por exemplo `estados/MG/radares.csv`.

**Como colocar os radares no GPS ou no celular?** Importe o `radares.gpx` (do Brasil ou do
seu estado) como pontos favoritos ou de interesse: OsmAnd, Organic Maps, Garmin BaseCamp e
a maioria dos apps de navegação aceitam GPX. No Google Earth, importe o `radares.kml`. O
Google My Maps aceita até 2.000 pontos por camada, então use o KML de um estado.

**Tem o limite de velocidade de cada rua e rodovia?** Onde existe dado: placas oficiais nas
rodovias federais concedidas, velocidade regulamentada das ruas do Rio de Janeiro e de São
Paulo e o `maxspeed` do OpenStreetMap no resto, mais os estimados na rede principal. Está em
`estados/<UF>/limites.csv`.

**Como abrir os limites no QGIS ou num mapa web?** Converta para GeoJSON com
`python scripts/to_geojson.py estados/<UF>/limites.csv`, ou abra o CSV direto no QGIS como camada
de texto delimitado, com `lng` como X e `lat` como Y.

**O que é limite estimado? Posso ignorar?** Quando a via não tem limite cadastrado, o
Pardal estima pela regra geral do Código de Trânsito para aquele tipo de via e marca a linha
com `estimated=1`. Pode estar errado. Para usar só limites sinalizados, descarte as linhas com
`estimated=1` (no consolidado do Brasil eles já vêm num arquivo separado).

**O radar vale para qual sentido?** Veja a coluna `direction_deg`: é o rumo do trânsito
fiscalizado, em graus (0 = norte, 90 = leste, 180 = sul, 270 = oeste). Para saber se um radar
vale para quem passa, compare com o rumo do veículo: se a diferença for de até 90°, vale. O
sentido vem das fontes oficiais que o informam ("crescente/decrescente" do km na ANTT e no
DER-GO, Norte/Sul/Leste/Oeste na Artesp, faixas por sentido no DER-SP, centro/bairro na CET de
São Paulo), convertido em rumo
pela geometria da rodovia (SNV do DNIT, malha estadual da Goinfra e OpenStreetMap). A tag
`direction` dos radares do OpenStreetMap não é usada: medida contra as fontes oficiais, ela
aponta o sentido contrário na maioria das rodovias (muitos mapeadores marcam para onde a câmera
olha). Vazio quer dizer os dois sentidos ou sentido não informado.

**Como sei se um radar ainda funciona?** A coluna `active` usa a situação informada pela
ANTT e pelos DERs, nas rodovias federais a validade da aferição no Inmetro e, na cidade de São
Paulo, os locais que a CET desativou.

**O que é um radar com fonte `INMETRO`?** O Inmetro registra todo medidor de velocidade aferido
do país, com a velocidade e a validade da aferição, mas sem coordenada: só o endereço. Quando o
endereço é "rua + número" ou um cruzamento, a posição vem do cadastro de endereços do IBGE (Censo
2022). Se há um radar conhecido a até 100 m, é o mesmo: ele ganha `+INMETRO` na coluna `source`
(aferição válida confirmada) e o limite, se não tinha. Se não há, sai um radar novo com
`source=INMETRO`. A posição desses é a do endereço, menos exata que a das outras fontes: medida
nas cinco capitais que têm lista oficial com coordenada, fica a 23 m do radar na mediana e a até
100 m em 89% dos casos; cerca de 1 em cada 10 pode estar a mais de 100 m do lugar certo.

**O mesmo radar aparece duas vezes?** Não: quando o OpenStreetMap e uma fonte oficial trazem o
mesmo radar (a até 30 m), ou dois órgãos publicam o mesmo radar, fica um ponto só, com a
posição, o limite e o sentido da fonte oficial, e a coluna `source` lista as fontes que o
confirmam. Pontos do OpenStreetMap a até 8 m um do outro também viram um só. Radares próximos
do mesmo órgão continuam separados: são equipamentos diferentes (um por pista, por faixa ou por
aproximação de cruzamento).

**Em que estado fica um ponto na divisa ou numa ponte?** Em um só: no estado cujo contorno
(malha oficial do IBGE) contém o ponto; ponte, orla e ilha que ficam fora do contorno vão para o
estado mais perto, a até 5 km. Ponte ou túnel na divisa sai nos dois estados.

**Posso usar no meu app ou projeto?** Pode, seguindo a licença ODbL (abaixo): citar as
fontes e manter a mesma licença em bases derivadas.

## Fontes

| Fonte | O que entra | Licença |
|---|---|---|
| OpenStreetMap (extratos da Geofabrik) | Radares, limites (`maxspeed`), pontes e túneis | ODbL 1.0 — © colaboradores do OpenStreetMap |
| DNIT — Controle de Velocidade (PNCV) | Radares das rodovias federais, com o sentido fiscalizado | Dado aberto governamental |
| DNIT — Sistema Nacional de Viação | Quilometragem das BRs, usada para casar com o Inmetro e para converter o sentido (crescente/decrescente) em rumo | Dado aberto governamental |
| ANTT — Radar | Radares das concessões federais, com situação e sentido | CC-BY |
| ANTT — Sinalização | Placas de velocidade máxima das concessões federais, por sentido | CC-BY |
| Inmetro — PSIE, medidores de velocidade | Situação da aferição (ativo/inativo) e, nas rodovias federais e nas estaduais de SP (DER-SP), o limite do radar que não trazia nenhum; nos medidores com endereço urbano, a confirmação do radar, o limite e os radares que nenhuma outra fonte tem | Creative Commons |
| DER-SP / Artesp, DER-GO (Goinfra), DER-PE | Radares das rodovias estaduais, com o sentido fiscalizado (SP e GO) | Dado aberto governamental |
| Goinfra — malha rodoviária estadual | Quilometragem das rodovias de Goiás, para converter o sentido em rumo | Dado aberto governamental |
| IBGE — malha das unidades da federação (API de malhas) | Contorno de cada estado, para decidir em que estado fica cada ponto | Dado aberto governamental |
| IBGE — CNEFE, cadastro de endereços do Censo 2022 | Coordenada do endereço dos medidores do Inmetro que só informam rua e número ou um cruzamento | Dado aberto governamental |
| Prefeitura do Rio de Janeiro (IPP, SMTR/CET-Rio) | Limite por trecho de rua e radares da cidade | CC-BY 4.0 |
| Prefeitura de São Paulo (GeoSampa, CET) | Limite pela classificação viária (vias de trânsito rápido e arteriais) e os radares ativos da cidade (locais fiscalizados da CET, com limite e sentido centro/bairro) | Dado aberto municipal |
| BHTrans (Belo Horizonte) | Radares e detectores de avanço de sinal | CC-BY |
| Detran-DF | Radares e lombadas eletrônicas | Dado aberto governamental |
| Prefeituras de Fortaleza, Recife e João Pessoa | Radares urbanos | Dado aberto governamental |

## Como os arquivos são gerados

O código que baixa as fontes, junta, calcula o sentido e gera todos os arquivos está em
[`gerador/`](gerador) (Python). Lá estão como rodar, como funciona cada parte, a
investigação das fontes com as medições ([`FONTES.md`](gerador/FONTES.md)) e o que ainda
falta ([`PENDENCIAS.md`](gerador/PENDENCIAS.md)).

## Licença

Os **dados** são uma base derivada do OpenStreetMap, distribuída sob a
**Open Database License (ODbL) 1.0** (texto em [`LICENSE`](LICENSE)).

- Cite "© colaboradores do OpenStreetMap" e as fontes da tabela acima.
- Se você publicar uma base derivada deste conjunto, ela precisa manter a ODbL.

O **código** (pasta `gerador/` e `scripts/`) é MIT ([`gerador/LICENSE`](gerador/LICENSE)).

## Atualização

O conjunto é gerado de novo periodicamente, porque as fontes oficiais mudam todo mês.
A data de cada pacote está no campo `built_at` do `catalogo.json`.

## English

**Pardal** is an open dataset of **speed cameras in Brazil** (fixed cameras, average-speed
sections, red-light cameras, with the enforced direction as a compass bearing) and **road speed limits**, with coordinates, as CSV, GeoJSON,
KML and GPX, for the whole country (`brasil/`) and per state (`estados/<UF>/`). It merges
official Brazilian open data (DNIT, ANTT, Inmetro, state road agencies, city halls) with
OpenStreetMap. Data licensed under the ODbL 1.0; the generator code (`gerador/`, Python) under
MIT. Not an official source; road signs always prevail.

<sub>Palavras-chave: radares Brasil, lista de radares, localização de radares, base de
radares, radares fixos, pardais, lombada eletrônica, radar de trecho, avanço de sinal,
fiscalização eletrônica, limite de velocidade, velocidade máxima das vias, rodovias
federais, rodovias estaduais, DNIT, ANTT, Inmetro, DER, arquivo de radares para GPS, POI de
radar, sentido do radar, GPX, KML, CSV, GeoJSON, OpenStreetMap, dados abertos, Brazil speed cameras, speed
limits Brazil, open data.</sub>
