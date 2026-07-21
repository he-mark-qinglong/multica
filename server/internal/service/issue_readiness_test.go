package service

import "testing"

func TestIsIssueReadyForDispatch(t *testing.T) {
	tests := []struct {
		name               string
		status             string
		childStatuses      []string
		dependencyStatuses []string
		want               bool
	}{
		{
			name:   "todo leaf without dependencies is ready",
			status: "todo",
			want:   true,
		},
		{
			name:          "todo parent with all children done is ready",
			status:        "todo",
			childStatuses: []string{"done", "done"},
			want:          true,
		},
		{
			name:          "todo issue with unfinished child is not ready",
			status:        "todo",
			childStatuses: []string{"done", "in_progress"},
			want:          false,
		},
		{
			name:               "todo issue with all children and dependencies done is ready",
			status:             "todo",
			childStatuses:      []string{"done", "done"},
			dependencyStatuses: []string{"done"},
			want:               true,
		},
		{
			name:               "todo issue with unfinished dependency is not ready",
			status:             "todo",
			dependencyStatuses: []string{"done", "blocked"},
			want:               false,
		},
		{
			name:   "non todo issue is not ready",
			status: "in_progress",
			want:   false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := IsIssueReadyForDispatch(test.status, test.childStatuses, test.dependencyStatuses)
			if got != test.want {
				t.Fatalf("IsIssueReadyForDispatch() = %v, want %v", got, test.want)
			}
		})
	}
}
