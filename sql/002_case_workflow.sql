ALTER TABLE cases
    ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN closed_at TIMESTAMPTZ,
    ADD CONSTRAINT cases_closure_consistent
        CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL));

CREATE TABLE case_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES cases(id),
    from_status TEXT NOT NULL CHECK (from_status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    to_status TEXT NOT NULL CHECK (to_status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    note TEXT NOT NULL CHECK (char_length(btrim(note)) BETWEEN 1 AND 2000),
    version INTEGER NOT NULL CHECK (version >= 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, version)
);
CREATE INDEX cases_run_status_idx ON cases(run_id, status);
