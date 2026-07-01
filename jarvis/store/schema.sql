CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  type TEXT NOT NULL,
  source TEXT NOT NULL,
  correlation_id TEXT,
  turn_id TEXT,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_turn_id ON events(turn_id);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  input_text TEXT,
  final_text TEXT,
  brain_adapter TEXT,
  brain_model TEXT,
  context_snapshot_json TEXT,
  error TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation_id ON turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_turns_created_at ON turns(created_at);
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(status);

CREATE TABLE IF NOT EXISTS memory_blocks (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source_event_id INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memory_blocks_kind ON memory_blocks(kind);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_active ON memory_blocks(active);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_priority ON memory_blocks(priority);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS worker_jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  worker_kind TEXT NOT NULL,
  prompt TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  result_summary TEXT,
  artifact_refs_json TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_worker_jobs_status ON worker_jobs(status);
CREATE INDEX IF NOT EXISTS idx_worker_jobs_created_at ON worker_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_worker_jobs_worker_kind ON worker_jobs(worker_kind);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  status TEXT NOT NULL,
  risk TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  action_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  decision_reason TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_created_at ON approvals(created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_risk ON approvals(risk);

CREATE TABLE IF NOT EXISTS tool_runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  turn_id TEXT,
  tool_name TEXT NOT NULL,
  status TEXT NOT NULL,
  risk TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  error TEXT,
  approval_id TEXT,
  FOREIGN KEY (approval_id) REFERENCES approvals(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_runs_turn_id ON tool_runs(turn_id);
CREATE INDEX IF NOT EXISTS idx_tool_runs_status ON tool_runs(status);
CREATE INDEX IF NOT EXISTS idx_tool_runs_tool_name ON tool_runs(tool_name);

CREATE TABLE IF NOT EXISTS voice_queue (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  turn_id TEXT,
  text TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  voice_id TEXT,
  interrupt_policy TEXT NOT NULL DEFAULT 'no_interrupt',
  status TEXT NOT NULL,
  error TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_voice_queue_status ON voice_queue(status);
CREATE INDEX IF NOT EXISTS idx_voice_queue_priority ON voice_queue(priority);
CREATE INDEX IF NOT EXISTS idx_voice_queue_turn_id ON voice_queue(turn_id);

CREATE TABLE IF NOT EXISTS listening_leases (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  released_at TEXT,
  expires_at TEXT NOT NULL,
  source TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  owner_process TEXT,
  turn_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_listening_leases_status ON listening_leases(status);
CREATE INDEX IF NOT EXISTS idx_listening_leases_expires_at ON listening_leases(expires_at);
CREATE INDEX IF NOT EXISTS idx_listening_leases_turn_id ON listening_leases(turn_id);

CREATE TABLE IF NOT EXISTS audio_device_snapshots (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  input_device_name TEXT,
  input_device_uid TEXT,
  output_device_name TEXT,
  output_device_uid TEXT,
  preferred_input TEXT,
  output_policy TEXT NOT NULL,
  bluetooth_microphone_allowed INTEGER NOT NULL DEFAULT 0,
  warning TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audio_device_snapshots_created_at
ON audio_device_snapshots(created_at);

CREATE TABLE IF NOT EXISTS runtime_process_observations (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  label TEXT,
  pid INTEGER,
  process_name TEXT,
  command TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  risk TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runtime_process_observations_created_at
ON runtime_process_observations(created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_process_observations_kind
ON runtime_process_observations(kind);
CREATE INDEX IF NOT EXISTS idx_runtime_process_observations_status
ON runtime_process_observations(status);
CREATE INDEX IF NOT EXISTS idx_runtime_process_observations_risk
ON runtime_process_observations(risk);
