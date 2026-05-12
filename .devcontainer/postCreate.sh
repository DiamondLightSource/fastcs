#!/usr/bin/env bash
# postCreate: run the claude-sandbox installer baked in by
# `just promote`. Idempotent so devcontainer rebuilds re-establish the
# shadow without re-downloading Claude.
set -euo pipefail

uv venv --clear
hash -r
uv sync && pre-commit install --install-hooks

bash .devcontainer/claude-sandbox/install.sh

# claude-sandbox: bring up the sandbox (added by just promote).
bash .devcontainer/claude-sandbox/install.sh
