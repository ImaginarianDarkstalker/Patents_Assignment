-- Final schema produced by ingest_and_clean.py

CREATE TABLE IF NOT EXISTS patents (
    patent_id TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    filing_date TEXT,        -- stored as 'YYYY-MM-DD'
    year TEXT                -- stored as string e.g. '2020'
);

CREATE TABLE IF NOT EXISTS inventors (
    inventor_id TEXT PRIMARY KEY,
    name TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    name TEXT
);

-- Actual relationship tables (normalised; no direct patent_relationships table exists)
CREATE TABLE IF NOT EXISTS patent_inventor (
    patent_id TEXT,
    rel_id TEXT,            -- inventor_id
    FOREIGN KEY (patent_id) REFERENCES patents(patent_id),
    FOREIGN KEY (rel_id) REFERENCES inventors(inventor_id)
);

CREATE TABLE IF NOT EXISTS patent_assignee (
    patent_id TEXT,
    company_id TEXT,
    FOREIGN KEY (patent_id) REFERENCES patents(patent_id),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

-- Compatibility view used by reports & app
CREATE VIEW IF NOT EXISTS patent_relationships AS
    SELECT pi.patent_id, pi.rel_id AS inventor_id, pa.company_id
    FROM patent_inventor pi
    LEFT JOIN patent_assignee pa USING (patent_id);