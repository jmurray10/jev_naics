-- JEV-52: spelling correction. Every word in the Census text (title, description, examples, index
-- entries, and activities sent here), lower case, 3+ letters, for trigram (pg_trgm) and edit-distance
-- (fuzzystrmatch) matching of words a user typed that the Census text does not contain.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE TABLE IF NOT EXISTS naics_word (
    word text PRIMARY KEY CHECK (word ~ '^[a-z]{3,}$')
);
CREATE INDEX IF NOT EXISTS naics_word_trgm ON naics_word USING gin (word gin_trgm_ops);
