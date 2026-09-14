#!/bin/bash
# Build a .deb package for ShareBoard.
#
# Usage (from project root, in a virtualenv):
#   pip install -e .
#   ./build_deb.sh
#
# Output: ../shareboard_<version>_all.deb
#
# Prereqs: dpkg-dev, debhelper, dh-python, python3-all, python3-flask,
# python3-flask-socketio, python3-socketio, python3-eventlet (Debian
# packages), plus `pip install --break-system-packages pycrdt` in the
# postinst step (pycrdt has no Debian package as of bookworm).

set -euo pipefail

PROJECT_ROOT="$(pwd)"
APP_NAME="shareboard"
VERSION="$(python3 -c 'import tomllib,pathlib;print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"

if [ ! -f "${PROJECT_ROOT}/pyproject.toml" ]; then
    echo "error: run from project root" >&2
    exit 1
fi

# Sanity check the layout the rules file expects.
for d in shareboard shareboard/templates shareboard/static debian; do
    if [ ! -d "$d" ]; then
        echo "error: missing $d" >&2
        exit 1
    fi
done

# Wipe any previous debian/<app> scratch tree.
rm -rf "debian/${APP_NAME}"

# dpkg-buildpackage picks up version from debian/changelog.
cat > debian/changelog <<EOF
${APP_NAME} (${VERSION}-1) unstable; urgency=medium

  * Automated build.

 -- ShareBoard <shareboard@example.invalid>  $(date -R)
EOF

# Bump compat to a modern level.
echo "13" > debian/compat

chmod +x debian/rules

dpkg-buildpackage -us -uc -b
