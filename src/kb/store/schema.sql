-- Knowledge store schema (DESIGN.md §5). Idempotent: safe to run on every start.
CREATE EXTENSION IF NOT EXISTS vector;

-- Layer 2: Raw Snapshot. Immutable: one row per observed version of a source record.
CREATE TABLE IF NOT EXISTS raw_versions (
    id              BIGSERIAL PRIMARY KEY,
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    source_version  TEXT NOT NULL,
    body_hash       TEXT NOT NULL,          -- content only
    acl_hash        TEXT NOT NULL,          -- permissions only
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    content         BYTEA NOT NULL,
    acl_raw         JSONB NOT NULL,         -- permissions as the source expresses them
    metadata        JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,   -- last change in the source
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_versions_record ON raw_versions (source_id, external_id);

-- Current state of every source record: which raw version is current, or a tombstone.
CREATE TABLE IF NOT EXISTS records (
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    current_raw_id  BIGINT REFERENCES raw_versions (id),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'tombstoned')),
    tombstoned_at   TIMESTAMPTZ,
    PRIMARY KEY (source_id, external_id)
);

-- Layer 3: Curated assets. Append-only versions; provenance points to raw versions.
CREATE TABLE IF NOT EXISTS asset_versions (
    id              BIGSERIAL PRIMARY KEY,
    asset_id        TEXT NOT NULL,
    version         INT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('knowledge', 'procedure', 'tool', 'example', 'memory')),
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    provenance      BIGINT[] NOT NULL,      -- raw_versions.id this asset was built from
    title           TEXT NOT NULL,
    section_path    TEXT NOT NULL,
    content         TEXT NOT NULL,
    url             TEXT NOT NULL,
    language        TEXT NOT NULL,
    -- Mandatory metadata, translated from the source permissions (pipeline step T3).
    owner           TEXT NOT NULL,
    department      TEXT NOT NULL,
    country         TEXT NOT NULL,          -- IT / CA / US / GLOBAL
    audience        TEXT NOT NULL CHECK (audience IN ('public', 'staff', 'hr', 'owner_only')),
    classification  TEXT NOT NULL CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
    mapping_note    TEXT,                   -- why a record fell back to owner_only, if it did
    replaces        TEXT,                   -- external_id of the record this one replaces
    source_updated_at TIMESTAMPTZ NOT NULL, -- last change in the source (for "most recent")
    valid_from      DATE,
    valid_to        DATE,
    last_synced     TIMESTAMPTZ NOT NULL,
    -- active: current; superseded: replaced by a newer version; stale: a derived asset
    -- whose source changed; tombstoned: source deleted; quarantined: excluded by the observer.
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'superseded', 'stale', 'tombstoned', 'quarantined')),
    embedding       vector(384),
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('simple', title || ' ' || content)) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, version)
);
CREATE INDEX IF NOT EXISTS asset_versions_record ON asset_versions (source_id, external_id);
CREATE INDEX IF NOT EXISTS asset_versions_tsv ON asset_versions USING gin (tsv);
CREATE INDEX IF NOT EXISTS asset_versions_embedding ON asset_versions USING hnsw (embedding vector_cosine_ops);

-- Facts extracted during curation, used to detect conflicts between sources
-- (DESIGN.md §1.2). POC: one deterministic extractor; production: entity layer + LLM.
CREATE TABLE IF NOT EXISTS asset_facts (
    asset_version_id BIGINT NOT NULL REFERENCES asset_versions (id),
    fact_key         TEXT NOT NULL,         -- e.g. vacation_days_per_year:US
    value            TEXT NOT NULL,
    PRIMARY KEY (asset_version_id, fact_key)
);
CREATE INDEX IF NOT EXISTS asset_facts_key ON asset_facts (fact_key);

-- Layer 4: Context Repos and their releases. A release pins exact asset versions.
CREATE TABLE IF NOT EXISTS context_repos (
    name            TEXT PRIMARY KEY,
    manifest        JSONB NOT NULL,
    active_release  BIGINT
);

CREATE TABLE IF NOT EXISTS releases (
    id              BIGSERIAL PRIMARY KEY,
    context_repo    TEXT NOT NULL REFERENCES context_repos (name),
    number          INT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('candidate', 'promoted', 'rejected', 'rolled_back')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL,
    manifest        JSONB NOT NULL,         -- pinned with the release (DESIGN.md §5.5)
    instructions    TEXT NOT NULL,          -- the agent's prompt, pinned with the release
    eval_report     JSONB,
    note            TEXT,
    UNIQUE (context_repo, number)
);

CREATE TABLE IF NOT EXISTS release_assets (
    release_id       BIGINT NOT NULL REFERENCES releases (id),
    asset_version_id BIGINT NOT NULL REFERENCES asset_versions (id),
    PRIMARY KEY (release_id, asset_version_id)
);

-- Sync bookkeeping per source: polling cursor, event offset, circuit-breaker freeze.
CREATE TABLE IF NOT EXISTS sync_state (
    source_id       TEXT PRIMARY KEY,
    cursor          TEXT,
    events_offset   INT NOT NULL DEFAULT 0,
    frozen          BOOLEAN NOT NULL DEFAULT false,
    frozen_reason   TEXT,
    last_sync_at    TIMESTAMPTZ
);

-- Incidents opened by the observer agent (DESIGN.md §8).
CREATE TABLE IF NOT EXISTS incidents (
    id              BIGSERIAL PRIMARY KEY,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind            TEXT NOT NULL,          -- release_rejected | active_regression | source_frozen
    target          TEXT NOT NULL,          -- context repo or source id
    signal_ref      TEXT NOT NULL,          -- what triggered it (e.g. release number); dedupe key
    status          TEXT NOT NULL CHECK (status IN ('open', 'mitigated', 'escalated', 'proposed')),
    report          TEXT NOT NULL,
    UNIQUE (kind, target, signal_ref)
);

-- Everything that changes state is recorded here (DESIGN.md §6.6, §7.3).
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    details         JSONB NOT NULL DEFAULT '{}'
);
