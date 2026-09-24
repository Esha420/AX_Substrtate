// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package tunnel

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type kubeConfig struct {
	CurrentContext string `yaml:"current-context"`
}

// CurrentContext returns the active Kubernetes context, checking:
// 1. Explicitly passed override
// 2. KUBECONTEXT environment variable
// 3. ~/.kube/config current-context (fast parse via YAML)
// 4. Fallback to 'kubectl config current-context'
func CurrentContext(override string) (string, error) {
	if override != "" {
		return override, nil
	}
	if env := os.Getenv("KUBECONTEXT"); env != "" {
		return env, nil
	}

	// Fast path: try reading kubeconfig directly
	kubeconfigPath := os.Getenv("KUBECONFIG")
	if kubeconfigPath == "" {
		home, err := os.UserHomeDir()
		if err == nil {
			kubeconfigPath = filepath.Join(home, ".kube", "config")
		}
	}

	if kubeconfigPath != "" && !strings.Contains(kubeconfigPath, string(os.PathListSeparator)) {
		if data, err := os.ReadFile(kubeconfigPath); err == nil {
			var cfg kubeConfig
			if err := yaml.Unmarshal(data, &cfg); err == nil && cfg.CurrentContext != "" {
				return cfg.CurrentContext, nil
			}
		}
	}

	// Fallback to kubectl CLI
	out, err := exec.Command("kubectl", "config", "current-context").Output()
	if err == nil {
		ctx := strings.TrimSpace(string(out))
		if ctx != "" {
			return ctx, nil
		}
	}
	return "", err
}
