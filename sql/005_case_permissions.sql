CREATE TABLE app_users (
    username TEXT PRIMARY KEY CHECK (username ~ '^[a-z][a-z0-9_-]{1,31}$'),
    role TEXT NOT NULL CHECK (role IN ('viewer', 'investigator', 'supervisor')),
    password_hash TEXT NOT NULL
);

ALTER TABLE case_events
    ADD COLUMN actor_username TEXT REFERENCES app_users(username),
    ADD COLUMN actor_role TEXT CHECK (actor_role IN ('investigator', 'supervisor')),
    ADD CONSTRAINT case_event_actor_pair
        CHECK ((actor_username IS NULL) = (actor_role IS NULL));
