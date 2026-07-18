-- Queryable run metrics: when a kind=metrics artifact is uploaded, its JSON
-- blob is parsed into one row here (unique on artifact_id) so agents and
-- humans can ask "all Sharpe values for campaign X" without parsing blobs.
-- task_id / issue_id are denormalized from the artifact row for query speed;
-- campaign / iteration come from artifact.meta (iteration stays TEXT because
-- agents emit both "83" and "iter#83").
CREATE TABLE run_metric (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    artifact_id   UUID NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
    task_id       UUID REFERENCES agent_task_queue(id) ON DELETE CASCADE,
    issue_id      UUID REFERENCES issue(id) ON DELETE SET NULL,
    campaign      TEXT NOT NULL DEFAULT '',
    iteration     TEXT NOT NULL DEFAULT '',
    sharpe        DOUBLE PRECISION,
    sortino       DOUBLE PRECISION,
    calmar        DOUBLE PRECISION,
    ann_return    DOUBLE PRECISION,
    max_drawdown  DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    oos_sharpe    DOUBLE PRECISION,
    oos_windows   INTEGER,
    timeframe     TEXT NOT NULL DEFAULT '',
    symbols       TEXT[],
    params        JSONB NOT NULL DEFAULT '{}',
    extra         JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT run_metric_artifact_unique UNIQUE (artifact_id)
);

CREATE INDEX idx_run_metric_workspace_campaign_iteration ON run_metric(workspace_id, campaign, iteration);
CREATE INDEX idx_run_metric_issue ON run_metric(issue_id) WHERE issue_id IS NOT NULL;
