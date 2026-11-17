#!/bin/bash
# Build the suite and pack it as the release archive (tasks, site bundles and
# OSWorld task configs for all 3,000 instances).
#   bash scripts/package_data.sh            -> dist/seam-suite-v1.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
python -m seam.build --out data/suite
python -m seam.gate --suite data/suite
mkdir -p dist
tar czf dist/seam-suite-v1.tar.gz -C data suite
sha256sum dist/seam-suite-v1.tar.gz | tee dist/seam-suite-v1.tar.gz.sha256
