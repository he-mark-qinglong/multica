package main

import (
	"strings"
	"testing"
)

// A typo'd subcommand must be an error (non-zero exit), never a silent help
// print — and close matches (including the "view" alias) must be suggested.
func TestGroupRunEUnknownCommand(t *testing.T) {
	err := groupRunE(issueCmd, []string{"veiw"})
	if err == nil {
		t.Fatal("expected error for unknown subcommand")
	}
	msg := err.Error()
	if !strings.Contains(msg, `unknown command "veiw" for "multica issue"`) {
		t.Errorf("missing unknown-command text: %q", msg)
	}
	// "veiw" is distance 2 from the "view" alias of `issue get`.
	if !strings.Contains(msg, "Did you mean this?") || !strings.Contains(msg, "\tget") {
		t.Errorf("expected suggestion of get via view alias: %q", msg)
	}
}

func TestGroupRunEExactNameSuggestion(t *testing.T) {
	err := groupRunE(issueCmd, []string{"lis"})
	if err == nil {
		t.Fatal("expected error for unknown subcommand")
	}
	if !strings.Contains(err.Error(), "\tlist") {
		t.Errorf("expected suggestion of list: %q", err.Error())
	}
}

func TestGroupRunEBareGroupShowsHelp(t *testing.T) {
	// Bare group prints help and returns nil (exit 0).
	if err := groupRunE(issueCmd, nil); err != nil {
		t.Errorf("bare group: expected nil error, got %v", err)
	}
}

func TestLevenshtein(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"view", "view", 0},
		{"veiw", "view", 2},
		{"lis", "list", 1},
		{"get", "status", 5},
	}
	for _, c := range cases {
		if got := levenshtein(c.a, c.b); got != c.want {
			t.Errorf("levenshtein(%q,%q) = %d, want %d", c.a, c.b, got, c.want)
		}
	}
}
