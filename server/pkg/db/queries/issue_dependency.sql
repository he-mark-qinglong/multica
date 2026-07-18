-- Project graph (map view): every issue in the project is a node, including
-- isolated ones with no edges.
-- name: ListProjectGraphIssues :many
SELECT id, number, title, status, priority, parent_issue_id
FROM issue
WHERE project_id = $1 AND workspace_id = $2
ORDER BY number;

-- Only edges whose BOTH endpoints live in this project — dangling edges to
-- issues outside the project would have no node to attach to on the map.
-- name: ListProjectGraphDependencies :many
SELECT d.id, d.issue_id, d.depends_on_issue_id, d.type
FROM issue_dependency d
JOIN issue a ON a.id = d.issue_id
JOIN issue b ON b.id = d.depends_on_issue_id
WHERE a.project_id = $1 AND b.project_id = $1
  AND a.workspace_id = $2 AND b.workspace_id = $2;

-- name: GetIssueDependencyByEndpoints :one
SELECT * FROM issue_dependency
WHERE issue_id = $1 AND depends_on_issue_id = $2 AND type = $3;

-- name: GetIssueDependency :one
SELECT * FROM issue_dependency
WHERE id = $1;

-- name: CreateIssueDependency :one
INSERT INTO issue_dependency (issue_id, depends_on_issue_id, type)
VALUES ($1, $2, $3)
RETURNING *;

-- name: DeleteIssueDependency :exec
DELETE FROM issue_dependency
WHERE id = $1;

-- Every edge touching a given issue, in either direction, with the
-- counterpart (other-end) issue's number/title joined in so callers can
-- render identifiers without a second lookup.
-- name: ListIssueDependencies :many
SELECT d.id, d.issue_id, d.depends_on_issue_id, d.type,
       c.id AS counterpart_id, c.number AS counterpart_number, c.title AS counterpart_title
FROM issue_dependency d
JOIN issue c ON c.id = CASE WHEN d.issue_id = $1 THEN d.depends_on_issue_id ELSE d.issue_id END
WHERE d.issue_id = $1 OR d.depends_on_issue_id = $1
ORDER BY d.id;

-- Cross-project edges: exactly one endpoint lives in this project. The
-- counterpart (external) issue's display fields are joined in so the map
-- can render the dashed external node without a second lookup. Edges to
-- issues with NULL project_id count as cross too (IS NOT DISTINCT FROM).
-- name: ListProjectCrossDependencies :many
SELECT d.id, d.issue_id, d.depends_on_issue_id, d.type,
       e.id AS ext_id, e.number AS ext_number, e.title AS ext_title,
       e.status AS ext_status, e.priority AS ext_priority,
       e.parent_issue_id AS ext_parent_issue_id, e.project_id AS ext_project_id
FROM issue_dependency d
JOIN issue a ON a.id = d.issue_id
JOIN issue b ON b.id = d.depends_on_issue_id
JOIN issue e ON e.id = CASE WHEN a.project_id IS NOT DISTINCT FROM $1 THEN d.depends_on_issue_id ELSE d.issue_id END
WHERE (a.project_id IS NOT DISTINCT FROM $1) <> (b.project_id IS NOT DISTINCT FROM $1)
  AND a.workspace_id = $2 AND b.workspace_id = $2
ORDER BY d.id;
