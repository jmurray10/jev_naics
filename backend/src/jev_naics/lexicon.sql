-- The lexicon database (E15): how people describe a business, mapped to Census wording. A SEPARATE database
-- from the NAICS one (Census tables are never touched); created by hand or by `lexicon_cli` against its own URL.
-- A term maps to words, never to a code. Every row says who reviewed it, when, and why.
CREATE TABLE IF NOT EXISTS naics_lexicon (
    term         text PRIMARY KEY CHECK (term = lower(term) AND length(term) > 0),
    census       text NOT NULL DEFAULT '',          -- Census wording; empty = recognised, adds nothing
    why          text NOT NULL,                      -- the industry the term usually means, and the Census text
    reviewed_by  text,
    reviewed_on  date,
    active       boolean NOT NULL DEFAULT true,
    added_from   text NOT NULL DEFAULT 'manual'      -- manual | mined (frequent out-of-vocabulary words, reviewed)
);
