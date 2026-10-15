-- JEV-50: every NAICS 2022 industry with its full official text, for full-text search.
-- Loaded from naics_2022_search.json (generated from the Census files); reloaded only when the
-- data version changes. `search` is set at insert time with weights:
--   A  the title
--   B  Census index entries, illustrative examples, and activities other industries'
--      cross-references send here ("grooming pets -- are classified in 812910")
--   C  the official description
-- An industry's own exclusions are kept for display and for Jev, never searched: they describe
-- what the industry is not.
CREATE TABLE IF NOT EXISTS naics_industry (
    code text PRIMARY KEY CHECK (code ~ '^[0-9]{6}$'),
    title text NOT NULL CHECK (title <> ''),
    description text NOT NULL,
    examples text[] NOT NULL,
    index_entries text[] NOT NULL,
    excludes jsonb NOT NULL,
    sent_here text[] NOT NULL,
    search tsvector NOT NULL
);
CREATE INDEX IF NOT EXISTS naics_industry_search ON naics_industry USING GIN (search);

-- How many industries use each word, for IDF ranking: a rare word ("grooming") says more about
-- an industry than a common one ("mobile", "services"). Rebuilt with every load.
CREATE TABLE IF NOT EXISTS naics_lexeme (
    word text PRIMARY KEY,
    ndoc integer NOT NULL CHECK (ndoc > 0)
);

CREATE TABLE IF NOT EXISTS naics_search_meta (
    key text PRIMARY KEY,
    value text NOT NULL
);
