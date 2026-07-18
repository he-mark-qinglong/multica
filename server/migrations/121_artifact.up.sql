-- Typed run artifacts: first-class, queryable outputs of agent task runs
-- (backtest metrics, equity curves, plots, logs, datasets). The blob lives
-- in object storage (same backend as attachments); this table holds the
-- metadata. `kind` is app-level validated (metrics|equity|plot|log|dataset|
-- other), not a DB enum, so new kinds don't need a migration.
CREATE TABLE artifact (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    task_id      UUID REFERENCES agent_task_queue(id) ON DELETE CASCADE,
    issue_id     UUID REFERENCES issue(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'other',
    name         TEXT NOT NULL,
    storage_key  TEXT NOT NULL,
    size_bytes   BIGINT NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT '',
    meta         JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_artifact_task ON artifact(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_artifact_workspace_kind ON artifact(workspace_id, kind);
CREATE INDEX idx_artifact_issue ON artifact(issue_id) WHERE issue_id IS NOT NULL;
