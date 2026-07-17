PRAGMA foreign_keys = ON;

CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE memory_blocks (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  priority INTEGER DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  active INTEGER DEFAULT 1,
  metadata TEXT DEFAULT '{}'
);

CREATE VIRTUAL TABLE memory_fts
USING fts5(id, kind, title, body, content='memory_blocks', content_rowid='rowid');

CREATE TABLE memory_inbox (
  id TEXT PRIMARY KEY,
  raw_text TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at REAL NOT NULL,
  processed INTEGER DEFAULT 0
);

CREATE TABLE compiled_contexts (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  turn_range_start INTEGER NOT NULL,
  turn_range_end INTEGER NOT NULL,
  created_at REAL NOT NULL,
  char_count INTEGER NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

INSERT INTO conversations VALUES ('conversation-1', 'Migrated conversation', 1720051200, 1720051210);
INSERT INTO turns (conversation_id, role, content, created_at)
VALUES ('conversation-1', 'user', 'fixture user turn', 1720051201),
       ('conversation-1', 'assistant', 'fixture assistant turn', 1720051202);
INSERT INTO memory_blocks VALUES
  ('memory-1', 'fact', 'Unique', 'unique fixture memory', 2, 1720051203, 1720051204, 1, '{"fixture":true}'),
  ('memory-2', 'fact', 'Duplicate', 'shared target memory', 0, 1720051203, 1720051204, 1, '{}');
INSERT INTO memory_fts(rowid, id, kind, title, body)
SELECT rowid, id, kind, title, body FROM memory_blocks;
INSERT INTO memory_inbox VALUES ('inbox-1', 'fixture inbox memory', 'fixture', 1720051205, 0);
INSERT INTO compiled_contexts VALUES ('context-1', 'conversation-1', 'fixture context summary', 1, 2, 1720051206, 23);
