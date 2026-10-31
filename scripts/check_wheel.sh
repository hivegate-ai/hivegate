#!/bin/bash

# Check the built wheel on its own, away from the checkout: `python -c` from the repo root
# would import the local api/ and pass whatever the wheel contains.
# Run after `uv build`. Used by CI and before publishing.

set -euo pipefail

WHEEL="$(cd dist && pwd)/$(cd dist && ls ./*.whl | head -1)"
WORK_DIR="$(mktemp -d)"
cd "${WORK_DIR}"

uvx --python 3.12 --from "${WHEEL}" hivegate --version
uvx --python 3.12 --from "${WHEEL}" hivegate init project
test -f project/compose.yaml
test -f project/db/migrations/setup.sql

uv run --isolated --no-project --python 3.12 --with "${WHEEL}" python -c \
  "import api.main; assert 'site-packages' in api.main.__file__, api.main.__file__"

echo "Wheel OK"
