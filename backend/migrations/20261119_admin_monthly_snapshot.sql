-- Explicit migration only. No DDL in API requests or scheduler startup.
-- No historical rows are fabricated. observed_at is the actual UTC capture time.
CREATE TABLE IF NOT EXISTS api_admin_monthly_snapshot (
    month DATE NOT NULL PRIMARY KEY,
    total_members BIGINT NOT NULL,
    active_subscriptions BIGINT NOT NULL,
    observed_at DATETIME NOT NULL
);
