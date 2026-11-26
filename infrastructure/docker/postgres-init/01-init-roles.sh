#!/bin/sh
set -eu

APP_PW="${METUSE_APP_PASSWORD:-change-me-app}"
DB_NAME="${POSTGRES_DB:-metuse}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB_NAME" <<EOSQL
SELECT set_config('metuse.init_app_pw', '${APP_PW}', false);
DO \$\$
DECLARE
    v_pw text := current_setting('metuse.init_app_pw');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metuse_app') THEN
        EXECUTE format(
            'CREATE ROLE metuse_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION PASSWORD %L',
            v_pw
        );
    ELSE
        EXECUTE format(
            'ALTER ROLE metuse_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION PASSWORD %L',
            v_pw
        );
    END IF;
END
\$\$;
GRANT CONNECT ON DATABASE "${DB_NAME}" TO metuse_app;
GRANT USAGE ON SCHEMA public TO metuse_app;
EOSQL
