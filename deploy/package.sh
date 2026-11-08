#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "$(git status --porcelain)" ]]; then
    echo 'Commit the deployment version first; the bundle contains committed source only.' >&2
    exit 1
fi
REVISION="$(git rev-parse --short HEAD)"
OUTPUT="${1:-/tmp/cqa-deploy-$REVISION.tar.gz}"
git archive --format=tar.gz --output="$OUTPUT" HEAD \
    app cqa config public scripts tests data/gold data/calibration deploy \
    requirements.txt requirements.lock.txt README.md AGENTS.md DESIGN.md docs/DEPLOYMENT.md \
    docs/comparisons/ours-fe8d2423ea82.json
echo "$OUTPUT"
