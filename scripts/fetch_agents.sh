#!/usr/bin/env bash
# Materialize every agent in agents.lock.yaml at its pinned commit.
#
# Idempotent: run any time. If the agent dir already has a .git, fetches
# and checks out the locked SHA; otherwise full-clones first. Exits non-zero
# if any agent's HEAD ends up != the pinned commit.
#
# Usage:
#   scripts/fetch_agents.sh              # all agents
#   scripts/fetch_agents.sh hermes       # one agent by name
#
# Requires: git, plus either yq (https://github.com/mikefarah/yq) or python3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCKFILE="${REPO_ROOT}/agents.lock.yaml"

if [[ ! -f "${LOCKFILE}" ]]; then
  echo "fetch_agents: ${LOCKFILE} not found" >&2
  exit 1
fi

# Emit one TSV line per agent: name<TAB>path<TAB>repo<TAB>commit
list_agents() {
  if command -v yq >/dev/null 2>&1; then
    yq -r '.agents | to_entries | .[] |
           [.key, .value.path, .value.repo, .value.commit] | @tsv' "${LOCKFILE}"
  else
    python3 - "${LOCKFILE}" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for name, spec in (data.get("agents") or {}).items():
    print("\t".join([name, spec["path"], spec["repo"], spec["commit"]]))
PY
  fi
}

filter="${1:-}"
rc=0

while IFS=$'\t' read -r name path repo commit; do
  [[ -z "${name}" ]] && continue
  if [[ -n "${filter}" && "${name}" != "${filter}" ]]; then
    continue
  fi

  target="${REPO_ROOT}/${path}"
  echo "==> ${name}: ${repo} @ ${commit:0:12}"

  if [[ ! -d "${target}/.git" ]]; then
    echo "    cloning into ${path}"
    git clone "${repo}" "${target}"
  else
    echo "    fetching"
    git -C "${target}" fetch --tags --prune origin
  fi

  git -C "${target}" checkout --detach "${commit}"

  actual=$(git -C "${target}" rev-parse HEAD)
  if [[ "${actual}" != "${commit}" ]]; then
    echo "    ERROR: HEAD is ${actual}, expected ${commit}" >&2
    rc=1
  else
    echo "    OK"
  fi
done < <(list_agents)

exit "${rc}"
