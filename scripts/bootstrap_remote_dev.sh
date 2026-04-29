#!/usr/bin/env bash
# Bootstrap an Ubuntu/EC2 machine for running HFA benchmarks.
#
# Usage:
#   bash scripts/bootstrap_remote_dev.sh
#
# The script is intentionally idempotent. It installs host tools, materializes
# pinned agent repos, writes .env.bench, and validates that the benchmark entry
# points are importable. It does not store API keys.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

log() {
  printf '\n==> %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

sudo_cmd() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ ! -f /etc/os-release ]]; then
  echo "bootstrap requires a Linux host with /etc/os-release" >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"ubuntu"* && "${ID_LIKE:-}" != *"debian"* ]]; then
  echo "warning: expected Ubuntu/Debian-like host, got ID=${ID:-unknown}" >&2
fi

log "Installing host packages"
sudo_cmd apt-get update
sudo_cmd apt-get install -y \
  ca-certificates \
  curl \
  docker.io \
  git \
  jq \
  python3 \
  python3-pip \
  python3-venv \
  python3-yaml

log "Starting Docker"
sudo_cmd systemctl enable --now docker
if [[ "${EUID}" -ne 0 ]]; then
  sudo_cmd usermod -aG docker "${USER}"
fi

log "Installing uv if needed"
if ! have uv; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"

BENCH_ROOT="${HFA_REMOTE_BENCH_ROOT:-}"
if [[ -z "${BENCH_ROOT}" ]]; then
  if [[ -d /mnt/bench && -w /mnt/bench ]]; then
    BENCH_ROOT="/mnt/bench/hfa"
  else
    BENCH_ROOT="${REPO_ROOT}/.bench"
  fi
fi

log "Configuring benchmark cache root at ${BENCH_ROOT}"
mkdir -p \
  "${BENCH_ROOT}/workspaces" \
  "${BENCH_ROOT}/hf-cache" \
  "${BENCH_ROOT}/uv-cache" \
  "${BENCH_ROOT}/swebench"

cat > "${REPO_ROOT}/.env.bench" <<EOF
export HFA_BENCH_WORKSPACE_ROOT="${BENCH_ROOT}/workspaces"
export HF_HOME="${BENCH_ROOT}/hf-cache"
export UV_CACHE_DIR="${BENCH_ROOT}/uv-cache"
export HFA_SWEBENCH_EVAL_TIMEOUT_SEC="\${HFA_SWEBENCH_EVAL_TIMEOUT_SEC:-7200}"
EOF

log "Materializing pinned agent repos"
bash scripts/fetch_agents.sh

log "Pre-warming benchmark Python environments"
source "${REPO_ROOT}/.env.bench"
uv run --project hermes_v-0-10-0 python --version
uv run --with swebench python -m swebench.harness.run_evaluation --help >/dev/null

log "Checking Docker access"
if docker ps >/dev/null 2>&1; then
  docker ps
else
  cat >&2 <<'EOF'
Docker is installed but this shell cannot access the Docker socket yet.
Run one of:
  newgrp docker
  exit and reconnect over SSH
Then verify:
  docker ps
EOF
fi

log "Bootstrap complete"
cat <<EOF
Environment file:
  source .env.bench

Set your model API key outside git:
  export HERMES_BENCH_API_KEY='...'

Smoke command:
  head -1 benchmark/families/F2_bug_fix/tasks_swebench_lite.jsonl > /tmp/tasks_swebench_lite_1.jsonl
  source .env.bench && uv run --project hermes_v-0-10-0 python -u benchmark/run_benchmark.py \\
    --family F2_bug_fix \\
    --tasks-file /tmp/tasks_swebench_lite_1.jsonl \\
    --config benchmark/configs/f2_swebench_lite_baseline.yaml \\
    --runner hermes_direct \\
    --out benchmark/results/f2_swebench_lite_smoke_1.jsonl \\
    --live-log
EOF
