# access-counter

Gera relatórios em formato COUNTER R5.


## Como instalar
__Criar ambiente virtual para python 3.6+__
```bash
virtualenv -p python3.6 .venv
```

__Acessar ambiente virtual__
```bash
source .venv/bin/activate
```

__Instalar dependências e pacotes__
```bash
apt install libmysqlclient-dev
pip install -r requirements.txt
python setup.py install
```


## Como usar

__Criar banco de dados e tabelas__

```sql
create database matomo
```

```bash
initialize_database -u STRING_CONNECTION
```


__Popular tabela de periódicos__

```bash
populate_journals -u STRING_CONNECTION
```

__Popular tabela de países por localização__

```bash
populate_localization_country -u STRING_CONNECTION --batch_size 5000
```

__Popular tabela de métricas de artigo por país para um dia__

```bash
populate_article_metric_country -u STRING_CONNECTION -d YYYY-MM-DD -c COLLECTION_ACRONYM
```

__Validar a tabela de métricas de artigo por país para um dia__

```bash
validate_article_metric_country -u STRING_CONNECTION -d YYYY-MM-DD -c COLLECTION_ACRONYM
```


__Calcular métricas COUNTER__

É preciso setar as variáveis de ambiente listadas ao final deste README.md

```bash
calculate_metrics \
    -c COLLECTION_ACRONYM \
    -u mysql://user:pass@host:port/database \
    --dict_date YYYY-MM-DD \
    --use_pretables
```

__Exportar dados para tabelas SUSHI__

É preciso setar as variáveis de ambiente listadas ao final deste README.md

```bash
export_to_database \
    -c COLLECTION_ACRONYM \
    -u mysql://user:pass@host:port/database \
    --auto
```

__Agregar tabelas__
```bash
usage: aggregate [-h] [-c COLLECTION] [-p PERIOD]
                 [-t {aggr_article_language_year_month_metric,aggr_journal_language_year_month_metric,aggr_journal_geolocation_year_month_metric,aggr_journal_language_yop_year_month_metric,aggr_journal_geolocation_yop_year_month_metric}]

optional arguments:
  -h, --help            show this help message and exit
  -c COLLECTION, --collection COLLECTION
                        Acrônimo de coleção
  -p PERIOD, --period PERIOD
                        Período de datas a serem agregadas (YYYY-MM-DD,YYYY-
                        MM-DD)
  -t {aggr_article_language_year_month_metric,aggr_journal_language_year_month_metric,aggr_journal_geolocation_year_month_metric,aggr_journal_language_yop_year_month_metric,aggr_journal_geolocation_yop_year_month_metric}, --tables {aggr_article_language_year_month_metric,aggr_journal_language_year_month_metric,aggr_journal_geolocation_year_month_metric,aggr_journal_language_yop_year_month_metric,aggr_journal_geolocation_yop_year_month_metric}
                        Tabelas a serem preenchidas
```


## Variáveis de ambiente
- COLLECTION
- MATOMO_ID_SITE
- ARTICLEMETA_DATABASE_STRINGorg:27017/articlemeta.articles?authSource
- LOG_FILE_DATABASE_STRING
- MATOMO_DATABASE_STRING
- DIR_DATA
- DIR_PRETABLES
- DIR_SUMMARY
- DIR_R5_HITS
- DIR_R5_METRICS
- LOGGING_LEVEL
- MATOMO_API_TOKEN
- MATOMO_DB_IP_COUNTER_LIMIT
- MATOMO_FIX_DATABASE_COLUMNS
- MATOMO_URL
- MIN_YEAR
- PRETABLE_DAYS_N
- COMPUTING_DAYS_N
- COMPUTING_TIMEDELTA
