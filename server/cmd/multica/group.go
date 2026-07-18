package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/spf13/cobra"
)

// groupRunE is the RunE for command groups (issue, agent, ...). Cobra's
// stock behavior for a group with a non-matching argument is to print help
// and exit 0 — a typo like `multica issue veiw` then looks successful to
// scripts. Instead: bare group prints help; unknown argument is an error
// with did-you-mean suggestions (subcommand names AND aliases, so a typo
// near the "view" alias points back at "get").
func groupRunE(cmd *cobra.Command, args []string) error {
	if len(args) == 0 {
		return cmd.Help()
	}
	typed := args[0]
	suggestions := cmd.SuggestionsFor(typed)
	for _, sub := range cmd.Commands() {
		if !sub.IsAvailableCommand() {
			continue
		}
		for _, alias := range sub.Aliases {
			if levenshtein(typed, alias) <= 2 && !containsString(suggestions, sub.Name()) {
				suggestions = append(suggestions, sub.Name())
			}
		}
	}
	msg := fmt.Sprintf("unknown command %q for %q", typed, cmd.CommandPath())
	if len(suggestions) > 0 {
		msg += "\n\nDid you mean this?\n\t" + strings.Join(suggestions, "\n\t")
	}
	return errors.New(msg)
}

func containsString(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// levenshtein computes the edit distance between two strings. Command and
// alias names are short, so the plain DP is fine.
func levenshtein(a, b string) int {
	ar, br := []rune(a), []rune(b)
	prev := make([]int, len(br)+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= len(ar); i++ {
		cur := make([]int, len(br)+1)
		cur[0] = i
		for j := 1; j <= len(br); j++ {
			cost := 1
			if ar[i-1] == br[j-1] {
				cost = 0
			}
			cur[j] = min(cur[j-1]+1, prev[j]+1, prev[j-1]+cost)
		}
		prev = cur
	}
	return prev[len(br)]
}
