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

package workspace_test

import (
	"context"
	"testing"

	"github.com/google/ax/internal/model"
	"github.com/google/ax/internal/store/memory"
	"github.com/google/ax/internal/workspace"
	"github.com/google/ax/pkg/apis/v1alpha1"
)

func TestWorkspacePlanner_PlanEnvironment(t *testing.T) {
	planner := workspace.NewPlannerWithConfig(
		model.Config{
			Provider:      model.ProviderGoogle,
			Model:         model.DefaultModel,
			DisableRemote: true,
		},
	)

	ws := &v1alpha1.Workspace{
		Metadata: &v1alpha1.ObjectMeta{
			Name:     "default-workspace",
			Atespace: "default",
		},
		Spec: &v1alpha1.WorkspaceSpec{
			Git: []*v1alpha1.GitRepo{
				{
					Name:   "origin",
					Repo:   "https://github.com/chalk/chalk.git",
					Branch: "main",
				},
			},
		},
	}

	plan, err := planner.PlanEnvironment(context.Background(), ws, "Setup a Go development environment")
	if err != nil {
		t.Fatalf("PlanEnvironment failed: %v", err)
	}

	if plan.WorkspaceName != "default-workspace" {
		t.Errorf("expected workspace 'default-workspace', got %s", plan.WorkspaceName)
	}
	if plan.ModelUsed != model.DefaultModel {
		t.Errorf("expected model %q, got %q", model.DefaultModel, plan.ModelUsed)
	}
	if plan.PlanSummary == "" {
		t.Errorf("expected non-empty plan summary")
	}
}

func TestWorkspacePlanner_FromStore(t *testing.T) {
	ctx := context.Background()
	s := memory.NewStore()

	customModel := &v1alpha1.Model{
		Metadata: &v1alpha1.ObjectMeta{
			Name:     "default-model",
			Atespace: "default",
		},
		Spec: &v1alpha1.ModelSpec{
			Provider: "google",
			Model:    "gemini-3.8-flash",
			SecretKey: &v1alpha1.SecretKeyRef{
				Name: "gemini-api-secret",
				Key:  "GEMINI_API_KEY",
			},
		},
	}
	if err := s.SaveModel(ctx, customModel); err != nil {
		t.Fatalf("failed to save model: %v", err)
	}

	t.Setenv("GEMINI_API_KEY", "test-key-1234")

	planner, err := workspace.NewPlannerFromStore(ctx, s, "default", model.WithDisableRemote(true))
	if err != nil {
		t.Fatalf("NewPlannerFromStore failed: %v", err)
	}

	ws := &v1alpha1.Workspace{
		Metadata: &v1alpha1.ObjectMeta{
			Name:     "store-workspace",
			Atespace: "default",
		},
		Spec: &v1alpha1.WorkspaceSpec{},
	}

	plan, err := planner.PlanEnvironment(ctx, ws, "Setup Go environment")
	if err != nil {
		t.Fatalf("PlanEnvironment failed: %v", err)
	}
	if plan.ModelUsed != "gemini-3.8-flash" {
		t.Errorf("expected model gemini-3.8-flash, got %s", plan.ModelUsed)
	}
}
