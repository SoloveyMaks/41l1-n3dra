-- Runs once on first initialization of the nedra database.
-- pgvector/pgvector:pg16 ships the extension.
CREATE EXTENSION IF NOT EXISTS vector;