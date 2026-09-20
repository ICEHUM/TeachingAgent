-- Run once as a PostgreSQL deployment administrator. Passwords are supplied by deployment.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'teaching_app') THEN
    CREATE ROLE teaching_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'langgraph_cp') THEN
    CREATE ROLE langgraph_cp LOGIN;
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS teaching_business;
CREATE SCHEMA IF NOT EXISTS langgraph_checkpoint;

REVOKE ALL ON SCHEMA teaching_business FROM PUBLIC;
REVOKE ALL ON SCHEMA langgraph_checkpoint FROM PUBLIC;

ALTER ROLE teaching_app SET search_path = teaching_business, public;
ALTER ROLE langgraph_cp SET search_path = langgraph_checkpoint, public;

REVOKE ALL ON SCHEMA langgraph_checkpoint FROM teaching_app;
REVOKE ALL ON SCHEMA teaching_business FROM langgraph_cp;

GRANT USAGE ON SCHEMA teaching_business TO teaching_app;
GRANT USAGE, CREATE ON SCHEMA langgraph_checkpoint TO langgraph_cp;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA teaching_business TO teaching_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA teaching_business TO teaching_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA teaching_business
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO teaching_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA teaching_business
  GRANT USAGE, SELECT ON SEQUENCES TO teaching_app;

REVOKE ALL ON ALL TABLES IN SCHEMA langgraph_checkpoint FROM teaching_app;
REVOKE ALL ON ALL TABLES IN SCHEMA teaching_business FROM langgraph_cp;
