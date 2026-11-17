CREATE TABLE case_ai_summaries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(id),
    case_version INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_snapshot JSONB NOT NULL,
    content JSONB NOT NULL,
    usage JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, case_version, model, prompt_version, input_hash)
);
CREATE INDEX ai_summaries_case_idx ON case_ai_summaries(case_id, id DESC);
