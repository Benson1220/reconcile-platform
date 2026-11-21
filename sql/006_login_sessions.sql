CREATE TABLE login_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX login_sessions_expiry_idx ON login_sessions(expires_at);
