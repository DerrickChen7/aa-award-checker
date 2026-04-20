CREATE TABLE IF NOT EXISTS routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    cabin        TEXT NOT NULL,
    max_miles    INTEGER NOT NULL,
    passengers   INTEGER NOT NULL DEFAULT 1,
    enabled        INTEGER NOT NULL DEFAULT 1,
    carrier_filter TEXT NOT NULL DEFAULT 'ac_only',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alert_state (
    route_id               INTEGER NOT NULL,
    flight_key             TEXT NOT NULL,
    last_seen_available_at TEXT NOT NULL,
    alerted_at             TEXT NOT NULL,
    PRIMARY KEY (route_id, flight_key),
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alert_state_last_seen
    ON alert_state (last_seen_available_at);
