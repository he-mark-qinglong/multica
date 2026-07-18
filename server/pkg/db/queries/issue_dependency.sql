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
