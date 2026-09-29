# Pendências do Pardal

Situação em 2026-09-29. O que não foi feito ainda e por quê.

## Manutenção

| Item | Situação |
|---|---|
| **Gerar e publicar de novo todo mês** | Futuro. As fontes mudam todo mês (DNIT, ANTT, Inmetro, OpenStreetMap). Hoje é manual: `python run.py datakit.build --all` (baixa o OpenStreetMap de novo quando a Geofabrik tem versão mais nova), `python scripts/publicar.py --commit --push`. Leva ~1 h e ~2 GB de download. Automatizável com GitHub Actions (cabe no disco e no limite de tempo dos runners). |
| **Zerar o histórico do git de tempos em tempos** | Combinado. Cada geração acrescenta dezenas de MB ao histórico e só a versão mais recente interessa a quem baixa. Quando o repositório passar de ~1 GB: criar um branch órfão com o estado atual, forçar o push no `main` e apagar o branch antigo. |

## Melhorias de dados

| Item | Por que ainda não | O que destrava |
|---|---|---|
| **Radares estaduais da Bahia** | A Seinfra-BA publicava um KML com os radares das rodovias estaduais, mas a página está suspensa pelo período eleitoral (outubro/2026). | Tentar de novo a partir de novembro: `ba.gov.br/infraestrutura/51/equipamentos-de-fiscalizacao-eletronica-das-rodovias-baianas`. Com o KML, é uma fonte nova no padrão de `sources/`. |
| **Cadastro atual do DF** | O portal `dados.df.gov.br` bloqueia download automático (checagem anti-robô). Hoje entra a camada ArcGIS do Detran-DF, que é de 2019. | Baixar o CSV à mão no navegador e ler de `data/raw/`, ou achar um endpoint sem a checagem. |
| **Radares do Inmetro sem coordenada** | ~8.900 medidores válidos têm só endereço urbano e ~2.500 só "rodovia estadual + km". Não há geocodificador nacional aberto com número de porta confiável, e a conversão de km (SNV e marcos do OSM) foi medida e é imprecisa demais para criar radar (ver `FONTES.md`). | Um geocodificador (por exemplo, Nominatim/Photon próprio com dados de endereço do Brasil) ou a base de quilometragem (SRE) de cada estado. |
| **Radares do Rio sem número de porta** | 468 dos 1.083 itens da lista da SMTR/CET-Rio têm endereço sem número ("acesso ao mergulhão…"); geocodificar pelo nome da rua cairia no meio dela. | Geocodificar por cruzamento ("RUA X com RUA Y") pelo geocodificador da prefeitura, ou casar com radares do OSM. |
| **Listas de DER-MG, DER-PR, DAER-RS, DER-ES, DER-RJ** | Só "rodovia + km" ou PDF, sem coordenada. | Mesma limitação da quilometragem estadual. |
| **Outras capitais** (Curitiba, Porto Alegre, Salvador, Goiânia, Manaus…) | Publicam só mapa na tela ou PDF sem coordenada. | Arquivo aberto com coordenada ou geocodificador da própria prefeitura (como o do Rio). |
| **Limites como linhas** | Hoje o limite é uma sequência de pontos. Para mapas, o ideal é o trecho da via como linha com o valor. | Guardar a geometria das vias no gerador; arquivos bem maiores, provavelmente só por estado. |
| **Sentido dos radares urbanos e estaduais sem campo de sentido** (Rio, BH, Detran-DF, DER-PE, Fortaleza, Recife, João Pessoa) | As bases não informam o sentido (ver "Sentido" em `FONTES.md`). Em cidade, radar costuma ficar num poste de um lado da rua, mas a coordenada publicada não é precisa o bastante para deduzir o lado. | Um campo de sentido nas bases, ou casar com radares do OSM que tenham `direction`. |
| **Conferir o sentido de SP em campo** | Artesp e DER-SP dão o sentido nominal da rodovia (Norte/Sul/Leste/Oeste), convertido pela via do OSM mais perto; não houve como medir a concordância como na ANTT e no DER-GO (sem km por vértice). | Comparar com radares do OSM de SP que têm `direction`, ou com o km da Artesp numa malha com quilometragem. |
| **Limites estimados nas estradas rurais** (`unclassified`) | Eram 75% do volume dos estimados (em Goiás, 135 mil km) e é onde a regra por tipo de via mais erra (40/60 km/h em estrada de terra). | Uma estimativa mais confiável (por exemplo, usando o tipo de superfície) e um formato que caiba no limite de 100 MB por arquivo. |

## Publicação

Os dados e este gerador ficam em github.com/Murkiriel/pardal. O código é MIT (`LICENSE`
desta pasta); os dados, ODbL 1.0. `data/` (downloads das fontes e resultados intermediários)
fica fora do repositório (`.gitignore` da raiz). Commits no repositório usam o e-mail anônimo
do GitHub (`…@users.noreply.github.com`), nunca o pessoal.
