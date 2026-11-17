CREATE TABLE import_batches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE source_files (
    batch_id BIGINT NOT NULL REFERENCES import_batches(id),
    source TEXT NOT NULL CHECK (source IN ('orders', 'payments', 'settlements')),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    original_content BYTEA NOT NULL,
    PRIMARY KEY (batch_id, source)
);
CREATE TABLE source_rows (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id BIGINT NOT NULL,
    source TEXT NOT NULL,
    line INTEGER NOT NULL CHECK (line >= 2),
    record JSONB NOT NULL,
    FOREIGN KEY (batch_id, source) REFERENCES source_files(batch_id, source),
    UNIQUE (batch_id, source, line)
);
CREATE TABLE orders (
    batch_id BIGINT NOT NULL REFERENCES import_batches(id),
    order_id TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    currency TEXT NOT NULL CHECK (currency = 'TWD'),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('PAID', 'UNPAID')),
    snapshot_at TIMESTAMPTZ NOT NULL,
    source_row_id BIGINT NOT NULL UNIQUE REFERENCES source_rows(id),
    PRIMARY KEY (batch_id, order_id)
);
CREATE TABLE payments (
    batch_id BIGINT NOT NULL,
    payment_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    currency TEXT NOT NULL CHECK (currency = 'TWD'),
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    paid_at TIMESTAMPTZ,
    expected_settlement_date DATE,
    source_row_id BIGINT NOT NULL UNIQUE REFERENCES source_rows(id),
    PRIMARY KEY (batch_id, payment_id),
    FOREIGN KEY (batch_id, order_id) REFERENCES orders(batch_id, order_id),
    CHECK ((status = 'SUCCESS' AND paid_at IS NOT NULL AND expected_settlement_date IS NOT NULL)
        OR (status = 'FAILED' AND paid_at IS NULL AND expected_settlement_date IS NULL))
);
CREATE TABLE settlements (
    batch_id BIGINT NOT NULL,
    settlement_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    gross_amount NUMERIC(18,2) NOT NULL CHECK (gross_amount >= 0),
    fee NUMERIC(18,2) NOT NULL CHECK (fee >= 0),
    net_amount NUMERIC(18,2) NOT NULL CHECK (net_amount >= 0),
    currency TEXT NOT NULL CHECK (currency = 'TWD'),
    settlement_date DATE NOT NULL,
    source_row_id BIGINT NOT NULL UNIQUE REFERENCES source_rows(id),
    PRIMARY KEY (batch_id, settlement_id),
    FOREIGN KEY (batch_id, payment_id) REFERENCES payments(batch_id, payment_id)
    -- 不約束 gross_amount - fee = net_amount，才能保存來源的業務異常。
);
CREATE TABLE reconciliation_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES import_batches(id),
    rules_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config JSONB NOT NULL,
    rules_executed JSONB NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (batch_id, rules_version, config_hash)
);
CREATE TABLE cases (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES reconciliation_runs(id),
    case_key TEXT NOT NULL,
    rule TEXT NOT NULL,
    order_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (run_id, case_key)
);
CREATE TABLE case_evidence (
    case_id BIGINT NOT NULL REFERENCES cases(id),
    position INTEGER NOT NULL,
    source_row_id BIGINT REFERENCES source_rows(id),
    payload JSONB NOT NULL,
    PRIMARY KEY (case_id, position)
);
CREATE INDEX cases_run_rule_idx ON cases(run_id, rule);
