-- Two schemas, one hard boundary (D5).
--
-- `core` is the bank's data. We read it and we cannot write it - and that is enforced
-- here, by grants, rather than by everyone remembering. A read-only port (ports/core_data.py
-- has no write methods) plus a read-only grant means two independent things would both
-- have to fail before we could corrupt their data.
--
-- `readycall` is ours: transcripts, briefs, matching decisions, wrap-ups.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS readycall;

-- The application role used for everything we generate.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readycall_rw') THEN
        CREATE ROLE readycall_rw LOGIN PASSWORD 'readycall_rw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readycall_ro') THEN
        CREATE ROLE readycall_ro LOGIN PASSWORD 'readycall_ro';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA readycall TO readycall_rw;
GRANT ALL PRIVILEGES ON SCHEMA readycall TO readycall_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA readycall
    GRANT ALL ON TABLES TO readycall_rw;

-- SELECT only. No INSERT, no UPDATE, no DELETE, ever.
GRANT USAGE ON SCHEMA core TO readycall_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO readycall_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO readycall_ro;

-- The read-write role may also read core, but still cannot write it.
GRANT USAGE ON SCHEMA core TO readycall_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO readycall_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO readycall_rw;
