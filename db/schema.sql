-- Teams Backup Scraper — PostgreSQL Schema
-- Run once to initialise the database, safe to re-run (IF NOT EXISTS everywhere).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- provides gen_random_uuid()

-- ─────────────────────────────────────────────────────────────────────────────
-- PROFESSOR
-- Populated automatically from the Teams channel metadata when first seen.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS professor (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    email       TEXT        UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CURSO  (the subject / discipline, e.g. "Calculus", "Data Structures")
-- Maps to a Microsoft Teams "Team".
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS curso (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    teams_id    TEXT        UNIQUE,       -- Graph API team ID, for re-sync
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CLASS  (a specific offering of a CURSO in a given semester)
-- Maps to a Teams channel inside the Team.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS class (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    curso_id      UUID        REFERENCES curso(id)     ON DELETE CASCADE,
    professor_id  UUID        REFERENCES professor(id) ON DELETE SET NULL,
    semester      TEXT        NOT NULL,   -- e.g. "Fall", "Spring", "2025/1"
    class_year    INT         NOT NULL,   -- e.g. 2025
    teams_channel_id TEXT     UNIQUE,     -- Graph API channel ID, for re-sync
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- STUDENT
-- A student who contributed a scrape run to the aggregated backup.
-- email is the unique identity (same as their institutional login).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS student (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    email       TEXT        UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- ARCHIVE  (one file from Teams, belonging to a class)
--
-- local_path    → container-internal path (legacy / local dev).
--                 e.g. /data/downloads/Calculus/file.pdf
--
-- s3_key        → S3 object key when the file is stored in the cloud.
--                 e.g. backup-teams/Calculus/General/file.pdf
--                 Null until the project migrates to S3 storage.
--
-- drive_item_id → stable Microsoft Graph API ID for the file.
--                 Does NOT change if the file is renamed or moved.
--                 Used as the idempotency key on re-runs.
--
-- etag          → Graph API ETag.  Changes when file content changes.
--                 When etag differs from stored value, the old local file is
--                 renamed to {name}_backup_{timestamp}.{ext} and the new
--                 version is downloaded to the original local_path.
--
-- contributed_by → the student whose scraper run first discovered the file.
--                  Nullable so solo / system runs don't require a student row.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archive (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id        UUID        NOT NULL REFERENCES class(id) ON DELETE CASCADE,
    contributed_by  UUID        REFERENCES student(id) ON DELETE SET NULL,
    file_name       TEXT        NOT NULL,
    file_extension  TEXT        NOT NULL,
    local_path      TEXT        NOT NULL,        -- legacy / local-dev path
    s3_key          TEXT,                        -- S3 object key (null until cloud migration)
    drive_item_id   TEXT        UNIQUE NOT NULL, -- Graph API stable ID
    etag            TEXT,                        -- change-detection token
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archive_drive_item    ON archive(drive_item_id);
CREATE INDEX IF NOT EXISTS idx_archive_class         ON archive(class_id);
CREATE INDEX IF NOT EXISTS idx_archive_contributed   ON archive(contributed_by);
CREATE INDEX IF NOT EXISTS idx_student_email         ON student(email);
