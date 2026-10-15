-- Run from the repo root: psql -v ON_ERROR_STOP=1 -f db/load.sql
--
-- The generated JSON (naics_2022_search.json, ~2.5 MB) is far too large for a psql -v value on
-- the command line: Linux caps a single argv string at MAX_ARG_STRLEN (128 KiB) and Windows caps
-- the whole command line at a few KiB, and a shell backtick/$() to build that value isn't portable
-- to Windows cmd either. So psql reads the file itself with \copy, which streams it client-side
-- (works on any host that can run psql, no server-side file access needed) straight from its path
-- below -- NOT a -v variable: psql does not interpolate :variables in a \copy file name (tested:
-- it is passed to the OS open() call literally, colon and all), so the path has to be written out.
-- It is one physical line per row into a temp table, reassembled into one jsonb value with
-- string_agg. DELIMITER and QUOTE are set to control bytes (\x01, \x02) that cannot appear in the
-- JSON text, so COPY's CSV mode does no delimiter/quote/escape interpretation and each line comes
-- back out byte for byte.
BEGIN;
CREATE TEMP TABLE _search_json_lines (n bigserial, line text) ON COMMIT DROP;
\copy _search_json_lines(line) FROM 'backend/src/jev_naics/data/naics_2022_search.json' WITH (FORMAT csv, DELIMITER E'\x01', QUOTE E'\x02')
CREATE TEMP TABLE _search_json ON COMMIT DROP AS
    SELECT (string_agg(line, E'\n' ORDER BY n))::jsonb AS doc FROM _search_json_lines;
DELETE FROM naics_industry;
INSERT INTO naics_industry (code, title, description, examples, index_entries, excludes, sent_here, search)
SELECT code, i->>'title', i->>'description',
       ARRAY(SELECT jsonb_array_elements_text(i->'examples')),
       ARRAY(SELECT jsonb_array_elements_text(i->'index_entries')),
       i->'excludes',
       ARRAY(SELECT jsonb_array_elements_text(i->'sent_here')),
       setweight(to_tsvector('english', i->>'title'), 'A')
       || setweight(to_tsvector('english', array_to_string(ARRAY(
              SELECT jsonb_array_elements_text(i->'index_entries') UNION ALL
              SELECT jsonb_array_elements_text(i->'examples') UNION ALL
              SELECT jsonb_array_elements_text(i->'sent_here')), ' ; ')), 'B')
       || setweight(to_tsvector('english', i->>'description'), 'C')
FROM jsonb_each((SELECT doc FROM _search_json) -> 'industries') AS e(code, i);
DELETE FROM naics_word;
-- Same word rule as jev_naics.db._WORDS: lower-case words of questions.VOCAB_MIN_WORD (3) letters
-- or more. Kept as a literal here (psql has no access to the Python constant); a mismatch would
-- only be caught by the naics_word parity check in backend/tests/test_db_build.py, so keep the two
-- in sync by hand if VOCAB_MIN_WORD ever changes.
INSERT INTO naics_word (word)
SELECT DISTINCT lower(w) FROM naics_industry,
     regexp_split_to_table(title || ' ' || description || ' ' || array_to_string(examples, ' ') || ' '
                           || array_to_string(index_entries, ' ') || ' ' || array_to_string(sent_here, ' '),
                           '[^A-Za-z]+') AS w
WHERE length(w) >= 3;
DELETE FROM naics_lexeme;
INSERT INTO naics_lexeme (word, ndoc) SELECT word, ndoc FROM ts_stat('SELECT search FROM naics_industry');
INSERT INTO naics_search_meta (key, value)
    VALUES ('version', (SELECT doc FROM _search_json) ->> 'version')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
COMMIT;
