#!/bin/bash
# Creates the dedicated test database alongside the development database.
# Runs exactly once, when the postgres_data volume is first initialised.
#
# Tests get their own database so a `DROP TABLE` in a fixture can never
# destroy development data.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE "$POSTGRES_DB_TEST";
EOSQL

echo "init: created test database '$POSTGRES_DB_TEST'"
