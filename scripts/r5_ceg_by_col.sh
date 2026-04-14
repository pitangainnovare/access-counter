#!/usr/bin/env bash

# Faz import de utilitários
source "$(dirname "$0")/r5_utils.sh"

# Define caminho da aplicacao Docker
DOCKER="$(which docker)"

# Define diretorio que contém arquivos que relacionam variaveis de ambiente
ENV_USAGE_COUNTER_COLLECTION=/opt/counter/info/env-usage-counter

# Diretorio de dicionarios
DIR_DICTIONARIES=/opt/counter/data/dictionaries

# Define volume
MAP_DATA_W=/opt/counter/data:/app/data

# Define listas e variaveis de acronimos
COLLECTIONS=(arg bol chl cic col cri cub dat ecu esp mex nbr per pre prt pry psi rve sss sza ury ven wid)
DISABLED_COLLECTIONS=(rvt scl ssp)
SUSHI_ACTIVATED_COLLECTIONS=(arg bol chl cic col cri cub ecu esp mex nbr per pre prt pry psi rve sss sza ury ven wid)
SUSHI_DISABLED_COLLECTIONS=(dat rvt scl ssp)
declare -A COLLECTION_TO_DOMAIN
COLLECTION_TO_DOMAIN=(
    ["arg"]=http://scielo.org.ar
    ["bol"]=http://scielo.org.bo
    ["chl"]=http://scielo.cl
    ["cic"]=http://cienciaecultura.bvs.br
    ["col"]=http://www.scielo.org.co
    ["cri"]=http://www.scielo.sa.cr
    ["cub"]=http://scielo.sld.cu
    ["dat"]=http://data.scielo.org
    ["ecu"]=http://scielo.senescyt.gob.ec
    ["esp"]=http://scielo.isciii.es
    ["mex"]=http://scielo.org.mx
    ["nbr"]=http://www.scielo.br
    ["per"]=http://www.scielo.org.pe
    ["pre"]=http://preprints.scielo.org
    ["prt"]=http://scielo.mec.pt
    ["pry"]=http://scielo.iics.una.py
    ["psi"]=http://pepsic.bvsalud.org
    ["rve"]=http://www.revenf.bvs.br
    ["scl"]=http://old.scielo.br
    ["ssp"]=http://scielosp.org
    ["sss"]=http://socialsciences.scielo.org
    ["sza"]=http://www.scielo.org.za
    ["ury"]=http://scielo.edu.uy
    ["ven"]=http://www.scielo.org.ve
    ["wid"]=http://westindies.scielo.org
)

# Define parametros automaticos
END_DATE="$(date '+%Y-%m-%d')"
BEGIN_DATE="$(date '+%Y-%m-%d' -d '-45 day')"
AGGR_FIRST_DATE="$(date '+%Y-%m-%d' -d '-45 day')"
AGGR_LAST_DATE="$(date '+%Y-%m-%d' -d '-5 day')"

COLLECTION=$1
CALCULATE=$2
EXPORT=$3
AGGREGATE=$4

DICT_DATE=$(detect_last_dict "$DIR_DICTIONARIES")

echo "SETTINGS"
echo "--------"
echo "BEGIN_DATE=$BEGIN_DATE"
echo "END_DATE=$END_DATE"
echo "OLD_DICT_DATE=$OLD_DICT_DATE"
echo "CALCULATE=$CALCULATE"
echo "AGGREGATE=$AGGREGATE"
echo "AGGR_FIRST_DATE=$AGGR_FIRST_DATE"
echo "AGGR_LAST_DATE=$AGGR_LAST_DATE"
echo "--------"

if [ "$CALCULATE" == "calculate" ]; then
    echo "Calculando acessos para $COLLECTION, dict $DICT_DATE e domain ${COLLECTION_TO_DOMAIN[$COLLECTION]}"
    $DOCKER run --rm \
        --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
        -v "$MAP_DATA_W" \
        access-counter:country-pilot \
        calculate_metrics \
        -c "$COLLECTION" \
        --dict_date "$DICT_DATE" \
        --domain "${COLLECTION_TO_DOMAIN[$COLLECTION]}"
fi

if [ "$EXPORT" == "export" ]; then
    echo "Exportando acessos para $COLLECTION, dict $DICT_DATE e domain ${COLLECTION_TO_DOMAIN[$COLLECTION]}"
    $DOCKER run --rm \
        --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
        -v "$MAP_DATA_W" \
        access-counter:country-pilot \
        export_to_database \
        --auto
fi

if [ "$AGGREGATE" == "aggregate" ]; then
    AGGREGATE_CONNECTION=${STR_CONNECTION:-$MATOMO_DATABASE_STRING}

    if [ -z "$AGGREGATE_CONNECTION" ]; then
        echo "ERRO: defina STR_CONNECTION ou MATOMO_DATABASE_STRING no ambiente antes de executar aggregate."
        exit 1
    fi

    echo "Agregando acessos para $COLLECTION, dict $DICT_DATE e domain ${COLLECTION_TO_DOMAIN[$COLLECTION]}"
    $DOCKER run --rm \
        --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
        --env STR_CONNECTION="$AGGREGATE_CONNECTION" \
        -v "$MAP_DATA_W" \
        access-counter:country-pilot \
        aggregate \
        -c "$COLLECTION" \
        --auto
fi
