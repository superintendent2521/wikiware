-- Initial PostgreSQL schema for WikiWare

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    password_changed_at TIMESTAMPTZ,
    last_collection_export_at TIMESTAMPTZ,
    page_edits JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_edits INTEGER NOT NULL DEFAULT 0,
    favorites JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS pages (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'Anonymous',
    edit_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    edit_permission TEXT NOT NULL DEFAULT 'everybody',
    allowed_users TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_title_branch ON pages (title, branch);
CREATE INDEX IF NOT EXISTS idx_pages_updated_at ON pages (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pages_search ON pages USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS history (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'Anonymous',
    edited_by TEXT,
    branch TEXT NOT NULL DEFAULT 'main',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    edit_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_title_branch_updated_at ON history (title, branch, updated_at DESC);

CREATE TABLE IF NOT EXISTS branches (
    id BIGSERIAL PRIMARY KEY,
    page_title TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_from TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_branches_title_name ON branches (page_title, branch_name);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS image_hashes (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size BIGINT,
    modified TIMESTAMPTZ,
    url TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_image_hashes_filename ON image_hashes (filename);
CREATE UNIQUE INDEX IF NOT EXISTS idx_image_hashes_sha256 ON image_hashes (sha256);

CREATE TABLE IF NOT EXISTS analytics_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    page_title TEXT,
    branch TEXT,
    referrer TEXT,
    query TEXT,
    query_normalized TEXT,
    results INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_analytics_timestamp ON analytics_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_analytics_query_normalized ON analytics_events (query_normalized);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_logs (
    id BIGSERIAL PRIMARY KEY,
    username TEXT,
    action TEXT NOT NULL,
    message TEXT NOT NULL,
    category TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs (category);
CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp ON system_logs (timestamp DESC);
