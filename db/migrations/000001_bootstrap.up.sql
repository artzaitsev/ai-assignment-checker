CREATE TABLE candidates (
  id BIGSERIAL PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE CHECK (public_id ~ '^cand_[0-9A-HJKMNP-TV-Z]{26}$'),
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX candidates_public_id_idx ON candidates (public_id);

CREATE TABLE assignments (
  id BIGSERIAL PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE CHECK (public_id ~ '^asg_[0-9A-HJKMNP-TV-Z]{26}$'),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX assignments_public_id_idx ON assignments (public_id);
CREATE INDEX assignments_active_idx ON assignments (is_active);

CREATE TABLE candidate_sources (
  id BIGSERIAL PRIMARY KEY,
  candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_external_id TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source_type, source_external_id)
);

CREATE INDEX candidate_sources_candidate_idx ON candidate_sources (candidate_id);

CREATE TABLE submissions (
  id BIGSERIAL PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE CHECK (public_id ~ '^sub_[0-9A-HJKMNP-TV-Z]{26}$'),
  candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
  assignment_id BIGINT NOT NULL REFERENCES assignments(id) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (
    status IN (
      'telegram_update_received',
      'telegram_ingest_in_progress',
      'uploaded',
      'normalization_in_progress',
      'normalized',
      'evaluation_in_progress',
      'evaluated',
      'delivery_in_progress',
      'delivered',
      'failed_normalization',
      'failed_evaluation',
      'failed_delivery',
      'failed_telegram_ingest',
      'dead_letter'
    )
  ),
  attempt_telegram_ingest INTEGER NOT NULL DEFAULT 0 CHECK (attempt_telegram_ingest >= 0),
  attempt_normalization INTEGER NOT NULL DEFAULT 0 CHECK (attempt_normalization >= 0),
  attempt_evaluation INTEGER NOT NULL DEFAULT 0 CHECK (attempt_evaluation >= 0),
  attempt_delivery INTEGER NOT NULL DEFAULT 0 CHECK (attempt_delivery >= 0),
  claimed_by TEXT,
  claimed_at TIMESTAMPTZ,
  lease_expires_at TIMESTAMPTZ,
  last_error_code TEXT,
  last_error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (
    (claimed_by IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL)
    OR (claimed_by IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)
  )
);

CREATE INDEX submissions_status_idx ON submissions (status);
CREATE INDEX submissions_reclaim_idx ON submissions (status, lease_expires_at) WHERE lease_expires_at IS NOT NULL;
CREATE INDEX submissions_public_id_idx ON submissions (public_id);
CREATE INDEX submissions_candidate_idx ON submissions (candidate_id);
CREATE INDEX submissions_assignment_idx ON submissions (assignment_id);

CREATE TABLE submission_sources (
  id BIGSERIAL PRIMARY KEY,
  submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL CHECK (source_type IN ('api_upload', 'telegram_webhook')),
  source_external_id TEXT NOT NULL,
  source_payload_ref TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source_type, source_external_id)
);

CREATE INDEX submission_sources_submission_idx ON submission_sources (submission_id);

CREATE TABLE artifacts (
  id BIGSERIAL PRIMARY KEY,
  submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  bucket TEXT NOT NULL,
  object_key TEXT NOT NULL,
  etag TEXT,
  content_type TEXT,
  size_bytes BIGINT,
  schema_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX artifacts_submission_idx ON artifacts (submission_id);

CREATE TABLE evaluations (
  id BIGSERIAL PRIMARY KEY,
  submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  score_1_10 INTEGER,
  criteria_scores_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  organizer_feedback_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  candidate_feedback_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ai_assistance_likelihood DOUBLE PRECISION,
  confidence DOUBLE PRECISION,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX evaluations_submission_idx ON evaluations (submission_id);

CREATE TABLE llm_runs (
  id BIGSERIAL PRIMARY KEY,
  submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  api_base TEXT,
  prompt_version TEXT NOT NULL,
  chain_version TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  result_schema_version TEXT NOT NULL,
  temperature DOUBLE PRECISION,
  seed BIGINT,
  tokens_input INTEGER,
  tokens_output INTEGER,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX llm_runs_submission_idx ON llm_runs (submission_id);

CREATE TABLE deliveries (
  id BIGSERIAL PRIMARY KEY,
  submission_id BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  external_message_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX deliveries_submission_idx ON deliveries (submission_id);
