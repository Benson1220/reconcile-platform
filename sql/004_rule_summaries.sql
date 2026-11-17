CREATE TABLE case_rule_summaries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(id),
    case_version INTEGER NOT NULL,
    rule_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_snapshot JSONB NOT NULL,
    content JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, case_version, rule_version, input_hash)
);
CREATE INDEX rule_summaries_case_idx ON case_rule_summaries(case_id, id DESC);
