#!/usr/bin/env bash
# postCreate: run the claude-sandbox installer baked in by
# `just promote`. Idempotent so devcontainer rebuilds re-establish the
# shadow without re-downloading Claude.
set -euo pipefail

uv venv --clear && uv sync && pre-commit install --install-hooks

bash install
