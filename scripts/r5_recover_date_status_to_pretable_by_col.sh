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

COLLECTION=$1
DATES=$2
RECOVER_CONNECTION=${STR_CONNECTION:-$MATOMO_DATABASE_STRING}

if [ -z "$DATES" ]; then
    echo "Erro: Forneça as datas separadas por vírgula ou 'all' (ex: ./scripts/r5_recover_date_status_to_pretable_by_col.sh cub all)"
    exit 1
fi

if [ -z "$RECOVER_CONNECTION" ]; then
    echo "ERRO: defina STR_CONNECTION ou MATOMO_DATABASE_STRING no ambiente antes de executar este script."
    exit 1
fi

echo "SETTINGS"
echo "--------"
echo "COLLECTION=$COLLECTION"
echo "DATES=$DATES"
echo "--------"

if [ "$DATES" == "all" ]; then
    echo "Revertendo status para PRETABLE (3) para TODAS as datas com status COMPUTED (4) na coleção $COLLECTION"
    $DOCKER run --rm \
        --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
        --env STR_CONNECTION="$RECOVER_CONNECTION" \
        -v "$MAP_DATA_W" \
        access-counter:country-pilot \
        recover_date_status_to_pretable \
        -c "$COLLECTION" \
        --all
else
    echo "Revertendo status para PRETABLE (3) para $COLLECTION nas datas $DATES"
    $DOCKER run --rm \
        --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
        --env STR_CONNECTION="$RECOVER_CONNECTION" \
        -v "$MAP_DATA_W" \
        access-counter:country-pilot \
        recover_date_status_to_pretable \
        -c "$COLLECTION" \
        -d "$DATES"
fi
