-- Talent Management (TM) Skills DB — PostgreSQL DDL
-- Target database: hr_data (tm schema provides logical separation from HR tables)
-- Purpose: Store employee skills, proficiency, and evidence for demo scenarios.
-- employee_ref is the TM system's local sync of HR master data.

BEGIN;

-- Create a dedicated schema
CREATE SCHEMA IF NOT EXISTS tm;
SET search_path TO tm;

-- =========
-- ENUM TYPES
-- =========
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                 WHERE t.typname = 'skill_category' AND n.nspname='tm') THEN
    CREATE TYPE tm.skill_category AS ENUM ('technical', 'functional', 'leadership', 'domain', 'tool', 'other');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                 WHERE t.typname = 'skill_source' AND n.nspname='tm') THEN
    CREATE TYPE tm.skill_source AS ENUM ('self', 'manager', 'assessment', 'certification', 'peer', 'inferred', 'system');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                 WHERE t.typname = 'evidence_type' AND n.nspname='tm') THEN
    CREATE TYPE tm.evidence_type AS ENUM ('certification', 'project', 'assessment', 'manager_validation', 'peer_endorsement', 'portfolio', 'work_history', 'other');
  END IF;
END $$;

-- ========================
-- ORG UNIT REFERENCE TABLE
-- ========================
-- Lightweight copy of HR org hierarchy for recursive org queries (endpoint #12)
CREATE TABLE IF NOT EXISTS tm.org_unit_ref (
  org_id                 TEXT PRIMARY KEY,
  org_name               TEXT NOT NULL,
  parent_org_id          TEXT REFERENCES tm.org_unit_ref(org_id),
  business_unit          TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_org_unit_ref_parent ON tm.org_unit_ref(parent_org_id);

-- ===================
-- EMPLOYEE REFERENCE
-- ===================
-- TM system's local sync of HR master data, enriched with job/org context
CREATE TABLE IF NOT EXISTS tm.employee_ref (
  employee_id            TEXT PRIMARY KEY,  -- key from HR DB
  display_name           TEXT,
  work_email             TEXT,
  job_title              TEXT,
  job_family             TEXT,
  org_id                 TEXT REFERENCES tm.org_unit_ref(org_id),
  org_name               TEXT,
  seniority_level        SMALLINT CHECK (seniority_level IS NULL OR seniority_level BETWEEN 1 AND 5),
  status                 TEXT DEFAULT 'active',  -- active/terminated/leave
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_employee_ref_org ON tm.employee_ref(org_id);
CREATE INDEX IF NOT EXISTS idx_employee_ref_status ON tm.employee_ref(status);
CREATE INDEX IF NOT EXISTS idx_employee_ref_job_family ON tm.employee_ref(job_family);

-- Trigger to keep updated_at current
CREATE OR REPLACE FUNCTION tm.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_employee_ref_updated_at ON tm.employee_ref;
CREATE TRIGGER trg_employee_ref_updated_at
BEFORE UPDATE ON tm.employee_ref
FOR EACH ROW
EXECUTE FUNCTION tm.set_updated_at();

-- ======
-- SKILLS
-- ======
CREATE TABLE IF NOT EXISTS tm.skill (
  skill_id               BIGSERIAL PRIMARY KEY,
  name                   TEXT NOT NULL UNIQUE,
  category               tm.skill_category NOT NULL DEFAULT 'other',
  description            TEXT,
  is_active              BOOLEAN NOT NULL DEFAULT TRUE,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_category ON tm.skill(category);
CREATE INDEX IF NOT EXISTS idx_skill_active ON tm.skill(is_active) WHERE is_active = TRUE;

-- ============================
-- EMPLOYEE <-> SKILL (PROFICIENCY)
-- ============================
-- proficiency: 0..5 (0 none, 5 expert)
-- confidence: 0..100
CREATE TABLE IF NOT EXISTS tm.employee_skill (
  employee_id            TEXT NOT NULL REFERENCES tm.employee_ref(employee_id) ON DELETE CASCADE,
  skill_id               BIGINT NOT NULL REFERENCES tm.skill(skill_id) ON DELETE CASCADE,
  proficiency            SMALLINT NOT NULL CHECK (proficiency BETWEEN 0 AND 5),
  confidence             SMALLINT NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  source                 tm.skill_source NOT NULL DEFAULT 'self',
  last_updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (employee_id, skill_id)
);

-- Helpful indexes for search/ranking
CREATE INDEX IF NOT EXISTS idx_employee_skill_skill ON tm.employee_skill(skill_id);
CREATE INDEX IF NOT EXISTS idx_employee_skill_prof ON tm.employee_skill(skill_id, proficiency DESC, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_employee_skill_employee ON tm.employee_skill(employee_id);
CREATE INDEX IF NOT EXISTS idx_employee_skill_updated ON tm.employee_skill(last_updated_at);

-- Trigger to auto-update last_updated_at on changes
CREATE OR REPLACE FUNCTION tm.set_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_employee_skill_updated_at ON tm.employee_skill;
CREATE TRIGGER trg_employee_skill_updated_at
BEFORE UPDATE ON tm.employee_skill
FOR EACH ROW
EXECUTE FUNCTION tm.set_last_updated_at();

-- ============
-- SKILL EVIDENCE
-- ============
CREATE TABLE IF NOT EXISTS tm.skill_evidence (
  evidence_id            BIGSERIAL PRIMARY KEY,
  employee_id            TEXT NOT NULL REFERENCES tm.employee_ref(employee_id) ON DELETE CASCADE,
  skill_id               BIGINT NOT NULL REFERENCES tm.skill(skill_id) ON DELETE CASCADE,
  evidence_type          tm.evidence_type NOT NULL,
  title                  TEXT NOT NULL,                 -- e.g., "AWS SAA", "Led X migration"
  issuer_or_system       TEXT,                          -- e.g., "Coursera", "Internal", "Manager"
  evidence_date          DATE,
  url_or_ref             TEXT,                          -- link or internal reference id
  signal_strength        SMALLINT NOT NULL CHECK (signal_strength BETWEEN 1 AND 5),
  notes                  TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common evidence queries
CREATE INDEX IF NOT EXISTS idx_skill_evidence_employee_skill ON tm.skill_evidence(employee_id, skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_skill ON tm.skill_evidence(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_strength ON tm.skill_evidence(skill_id, signal_strength DESC, evidence_date DESC);

COMMIT;
