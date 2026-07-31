package handler

import (
	"testing"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

func TestDifficultyModelForLabels(t *testing.T) {
	tests := []struct {
		name   string
		labels []db.IssueLabel
		want   string
		ok     bool
	}{
		{"none", nil, "", false},
		{"unrelated", []db.IssueLabel{{Name: "bug"}, {Name: "infra"}}, "", false},
		{"trivial", []db.IssueLabel{{Name: "difficulty:trivial"}}, "minimax-m3", true},
		{"medium", []db.IssueLabel{{Name: "difficulty:medium"}}, "glm-5.2-smark", true},
		{"hard", []db.IssueLabel{{Name: "difficulty:hard"}}, "managed:kimi-tang/k3", true},
		{"first-wins", []db.IssueLabel{{Name: "difficulty:hard"}, {Name: "difficulty:trivial"}}, "managed:kimi-tang/k3", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := difficultyModelForLabels(tt.labels)
			if got != tt.want || ok != tt.ok {
				t.Errorf("got (%q,%v), want (%q,%v)", got, ok, tt.want, tt.ok)
			}
		})
	}
}

func TestDifficultyModelOverrideEnabled(t *testing.T) {
	t.Setenv("MULTICA_DIFFICULTY_MODEL_OVERRIDE", "")
	if !difficultyModelOverrideEnabled() {
		t.Error("default should be enabled")
	}
	t.Setenv("MULTICA_DIFFICULTY_MODEL_OVERRIDE", "true")
	if !difficultyModelOverrideEnabled() {
		t.Error("explicit true should be enabled")
	}
	t.Setenv("MULTICA_DIFFICULTY_MODEL_OVERRIDE", "false")
	if difficultyModelOverrideEnabled() {
		t.Error("false should disable")
	}
}
