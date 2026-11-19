-- Explicit additive migration: preserve existing major/minor category pairs.
-- Standalone majors are selectable before any minor category exists.
CREATE TABLE IF NOT EXISTS api_keyword_major (
    major_name VARCHAR(40) NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL
);
