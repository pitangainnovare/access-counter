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
REVERT_CONNECTION=${STR_CONNECTION:-$MATOMO_DATABASE_STRING}

if [ -z "$REVERT_CONNECTION" ]; then
    echo "ERRO: defina STR_CONNECTION ou MATOMO_DATABASE_STRING no ambiente antes de executar este script."
    exit 1
fi

echo "SETTINGS"
echo "--------"
echo "COLLECTION=$COLLECTION"
echo "--------"

echo "Revertendo status pendentes para $COLLECTION"
$DOCKER run --rm \
    --env-file="$ENV_USAGE_COUNTER_COLLECTION"-"$COLLECTION" \
    --env STR_CONNECTION="$REVERT_CONNECTION" \
    -v "$MAP_DATA_W" \
    access-counter:country-pilot \
    revert_date_status_for_pending_aggr \
    -c "$COLLECTION"
