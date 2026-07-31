package handler

import (
	"os"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// difficultyModelMap maps an issue's difficulty:* label to a kimi config
// [models.X] key. Each key is a valid modelId for kimi's session/set_model
// RPC and binds a distinct provider (minimax / glm-smark / kimi-tang). The
// override relies on the claiming agent running on a kimi runtime whose
// config.toml defines these entries. Keep in sync with ~/.kimi-code/config.toml
// and the ROUTING table in quant-loop/scripts/task_router.py.
var difficultyModelMap = map[string]string{
	"difficulty:trivial": "minimax-m3",
	"difficulty:medium":  "glm-5.2-smark",
	"difficulty:hard":    "managed:kimi-tang/k3",
}

// difficultyModelOverrideEnabled honours a kill switch so the override can
// be turned off without a redeploy. Default on; set
// MULTICA_DIFFICULTY_MODEL_OVERRIDE=false to disable.
func difficultyModelOverrideEnabled() bool {
	return os.Getenv("MULTICA_DIFFICULTY_MODEL_OVERRIDE") != "false"
}

// difficultyModelForLabels returns the model id for the first difficulty:*
// label present on the issue, if any.
func difficultyModelForLabels(labels []db.IssueLabel) (string, bool) {
	for _, l := range labels {
		if m, ok := difficultyModelMap[l.Name]; ok {
			return m, true
		}
	}
	return "", false
}
