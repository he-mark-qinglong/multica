package service

// IsIssueReadyForDispatch reports whether an issue can be dispatched.
// An issue is ready only when it is todo, all children (if any) are done,
// and all depends_on issues (if any) are done.
func IsIssueReadyForDispatch(status string, childStatuses, dependencyStatuses []string) bool {
	if status != "todo" {
		return false
	}

	return allIssuesDone(childStatuses) && allIssuesDone(dependencyStatuses)
}

func allIssuesDone(statuses []string) bool {
	for _, status := range statuses {
		if status != "done" {
			return false
		}
	}
	return true
}
