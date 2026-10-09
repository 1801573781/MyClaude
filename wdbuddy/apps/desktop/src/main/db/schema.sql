PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tasks        (id TEXT PRIMARY KEY, title TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('ask','plan','craft')), status TEXT NOT NULL,
  workspace_uri TEXT, permission TEXT NOT NULL DEFAULT 'default',
  model_id TEXT NOT NULL DEFAULT 'auto', max_context INTEGER NOT NULL DEFAULT 0,
  trigger_json TEXT NOT NULL DEFAULT '{"type":"manual"}',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS messages     (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, meta_json TEXT,
  created_at INTEGER NOT NULL, UNIQUE(task_id, seq));
CREATE TABLE IF NOT EXISTS steps        (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  seq INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, summary TEXT,
  started_at INTEGER, ended_at INTEGER);
CREATE TABLE IF NOT EXISTS artifacts    (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL, path TEXT, mime_type TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  sha256 TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints  (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
  step_id TEXT, snapshot_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS preferences (id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, value TEXT NOT NULL,
  origin TEXT NOT NULL CHECK(origin IN ('auto','user')), updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings     (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
