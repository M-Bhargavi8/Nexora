-- Enable the pgvector extension.
--
-- Runs once, automatically, when the database container initialises an empty
-- data directory. Enabling it now rather than in Phase 6 means we find out
-- immediately if anything is wrong with the image, instead of discovering it
-- months later when the RAG pipeline is being built.
--
-- `vector` adds a column type for embeddings plus the distance operators
-- (<->, <=>, <#>) used for similarity search.

CREATE EXTENSION IF NOT EXISTS vector;
