# Revisão Técnica de `idformat`

## Resumo
`format` hoje influencia mais do que o armazenamento final. Ele aparece no parsing do hit, no agrupamento em memória, no filtro de double-click, nas tabelas diárias legadas e nas agregações de periódico. Isso significa que remover `idformat` do schema novo é seguro nesta primeira fase, mas remover `format` da semântica de contagem exige uma segunda etapa dedicada.

## Classificação dos usos atuais

### 1. Necessário para parsing e classificação do hit
- `libs/lib_hit.py` obtém `format` a partir da URL/ação.
- `models/hit.py` propaga `format` para o objeto `Hit`.
- `libs/lib_hit.py` usa `format` como um dos sinais para chegar ao `content_type`.

Leitura: aqui `format` ainda é útil como insumo de interpretação do acesso.

### 2. Legado de armazenamento e cardinalidade
- `models/declarative.py` inclui `idformat` na chave da CAM legada.
- `models/declarative.py` inclui `idformat_cjm` na chave de `counter_journal_metric`.
- `proc/export_to_database.py` ainda monta chaves de persistência com `idformat` para as tabelas legadas.

Leitura: esse uso inflaciona cardinalidade e é o principal candidato a remoção no modelo novo.

### 3. Provável desvio ou ponto sensível frente ao COUNTER R5.1
- `models/hit.py` inclui `format` na chave de agrupamento do artigo em memória.
- `libs/lib_counter.py` inclui `format` na comparação de double-click para artigo.
- `models/counter.py` calcula `unique_*` por sessão e `content_type`, mas esse cálculo herda a fragmentação anterior introduzida pela chave com `format`.

Leitura: aqui está o núcleo da revisão futura. O COUNTER R5.1 é orientado a item e `content_type`; usar `format` na fragmentação da chave pode influenciar `unique_*` e o comportamento do filtro de double-click.

## Impacto nas métricas
- `requests` e `investigations` hoje dependem principalmente de `content_type`, não de `format`.
- `total_*` pode continuar estável sem `idformat` no armazenamento novo, desde que o cálculo bruto permaneça igual.
- `unique_*` é o ponto mais sensível, porque a chave de artigo e o double-click ainda carregam `format`.

## Decisão desta fase
- Remover `idformat` apenas do armazenamento novo:
  - `counter_article_metric_day`
  - `counter_article_metric_country_language_month`
- Manter intacta a semântica atual de:
  - agrupamento de hit
  - double-click filtering
  - cálculo de `unique_*`

## Próxima fase recomendada
- Revisar a chave de agrupamento de artigo em `models/hit.py`.
- Revisar a comparação de artigo em `libs/lib_counter.py`.
- Comparar, com amostras reais, o efeito de tirar `format` sobre:
  - `total_item_requests`
  - `total_item_investigations`
  - `unique_item_requests`
  - `unique_item_investigations`
