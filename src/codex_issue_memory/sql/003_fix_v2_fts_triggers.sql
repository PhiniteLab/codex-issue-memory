DROP TRIGGER IF EXISTS issue_variants_ai;
DROP TRIGGER IF EXISTS issue_variants_au;
DROP TRIGGER IF EXISTS issue_variants_ad;
DROP TRIGGER IF EXISTS issue_episodes_ai;
DROP TRIGGER IF EXISTS issue_episodes_au;
DROP TRIGGER IF EXISTS issue_episodes_ad;

CREATE TRIGGER IF NOT EXISTS issue_variants_ai AFTER INSERT ON issue_variants BEGIN
  INSERT INTO issue_variants_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_variants_ad AFTER DELETE ON issue_variants BEGIN
  INSERT INTO issue_variants_fts(issue_variants_fts, rowid, search_text)
  VALUES('delete', old.id, old.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_variants_au AFTER UPDATE ON issue_variants BEGIN
  INSERT INTO issue_variants_fts(issue_variants_fts, rowid, search_text)
  VALUES('delete', old.id, old.search_text);
  INSERT INTO issue_variants_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_ai AFTER INSERT ON issue_episodes BEGIN
  INSERT INTO issue_episodes_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_ad AFTER DELETE ON issue_episodes BEGIN
  INSERT INTO issue_episodes_fts(issue_episodes_fts, rowid, search_text)
  VALUES('delete', old.id, old.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_au AFTER UPDATE ON issue_episodes BEGIN
  INSERT INTO issue_episodes_fts(issue_episodes_fts, rowid, search_text)
  VALUES('delete', old.id, old.search_text);
  INSERT INTO issue_episodes_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

INSERT INTO issue_variants_fts(issue_variants_fts) VALUES('rebuild');
INSERT INTO issue_episodes_fts(issue_episodes_fts) VALUES('rebuild');
