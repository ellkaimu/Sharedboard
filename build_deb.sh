#!/bin/bash
# Build a .deb package for ShareBoard.
#
# Usage (from project root):
#   ./build_deb.sh
#
# Output: ../shareboard_<version>-1_all.deb
#
# This script picks one of two paths:
#
#   1) Proper path — uses dpkg-buildpackage + pybuild. Requires:
#        sudo apt install dpkg-dev debhelper dh-python \
#                         python3-all python3-setuptools \
#                         python3-wheel pybuild-plugin-pybuild \
#                         python3-flask python3-flask-socketio \
#                         python3-socketio python3-eventlet
#      And to actually run, the postinst still pip-installs pycrdt
#      because pycrdt isn't in Debian bookworm.
#
#   2) Manual path — uses dpkg-deb directly. No dpkg-dev, no debhelper,
#      no root. Produces a working .deb that depends on the same set of
#      system Python packages. Use this when you don't have sudo.

set -euo pipefail

PROJECT_ROOT="$(pwd)"
APP_NAME="shareboard"

# Read version from pyproject.toml without depending on tomllib (3.11+).
VERSION="$(grep -E '^version[[:space:]]*=' "${PROJECT_ROOT}/pyproject.toml" \
    | head -n1 \
    | sed -E 's/.*"([^"]+)".*/\1/')"
if [ -z "${VERSION}" ]; then
    echo "error: could not parse version from pyproject.toml" >&2
    exit 1
fi
PKG_VERSION="${VERSION}-1"
echo "==> Building ${APP_NAME} ${PKG_VERSION}"

# Sanity check the layout.
for d in shareboard shareboard/templates shareboard/static debian; do
    if [ ! -d "${PROJECT_ROOT}/${d}" ]; then
        echo "error: missing ${d}" >&2
        exit 1
    fi
done

if command -v dpkg-buildpackage >/dev/null 2>&1 \
   && command -v debhelper >/dev/null 2>&1; then
    # -------------------------------------------------------------- proper path
    rm -rf "debian/${APP_NAME}"

    cat > debian/changelog <<EOF
${APP_NAME} (${PKG_VERSION}) unstable; urgency=medium

  * Automated build.

 -- ShareBoard <shareboard@example.invalid>  $(date -R)
EOF
    echo "13" > debian/compat
    chmod +x debian/rules

    dpkg-buildpackage -us -uc -b
    echo
    echo "Built: ../${APP_NAME}_${PKG_VERSION}_all.deb"
else
    # --------------------------------------------------------------- manual path
    echo "==> dpkg-buildpackage / debhelper not found; using dpkg-deb directly"

    if ! command -v dpkg-deb >/dev/null 2>&1; then
        echo "error: dpkg-deb not found either. Install it with:" >&2
        echo "  sudo apt install dpkg" >&2
        exit 1
    fi

    STAGE="$(mktemp -d -t shareboard-deb-XXXXXX)"
    DEST="${STAGE}/root"
    DEBIAN_DIR="${DEST}/DEBIAN"
    cleanup() { rm -rf "${STAGE}"; }
    trap cleanup EXIT

    mkdir -p \
        "${DEBIAN_DIR}" \
        "${DEST}/usr/bin" \
        "${DEST}/usr/lib/python3/dist-packages" \
        "${DEST}/etc/systemd/system" \
        "${DEST}/usr/share/applications" \
        "${DEST}/var/lib/shareboard"

    # --- Python package -------------------------------------------------------
    # Copy our package tree straight in, sans dev artifacts.
    PKG_SRC="$(mktemp -d -t shareboard-src-XXXXXX)"
    trap 'rm -rf "${STAGE}" "${PKG_SRC}"' EXIT
    cp -r "${PROJECT_ROOT}/shareboard" "${PKG_SRC}/shareboard"
    find "${PKG_SRC}" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "${PKG_SRC}" -type f -name '*.pyc' -delete
    cp -r "${PKG_SRC}/shareboard" \
        "${DEST}/usr/lib/python3/dist-packages/"

    # Minimal .dist-info so pip / systemd-aware tools can recognise the install.
    DIST_INFO="${DEST}/usr/lib/python3/dist-packages/shareboard-${VERSION}.dist-info"
    mkdir -p "${DIST_INFO}"
    cat > "${DIST_INFO}/METADATA" <<EOF
Metadata-Version: 2.1
Name: shareboard
Version: ${VERSION}
Summary: Real-time collaborative text editor (Google-Docs style) on pycrdt
EOF
    cat > "${DIST_INFO}/RECORD" <<EOF
shareboard/__init__.py,,,
shareboard/__main__.py,,,
shareboard/app.py,,,
shareboard/config.py,,,
shareboard/rooms.py,,,
shareboard/socket_handlers.py,,,
shareboard/storage.py,,,
shareboard/sync.py,,,
shareboard/templates/base.html,,,
shareboard/templates/board.html,,,
shareboard/templates/index.html,,,
shareboard/static/css/style.css,,,
shareboard/static/js/editor.js,,,
../../../bin/shareboard,,,
shareboard-${VERSION}.dist-info/METADATA,,,
shareboard-${VERSION}.dist-info/RECORD,,,
EOF

    # --- Launcher script ------------------------------------------------------
    cat > "${DEST}/usr/bin/shareboard" <<'EOF'
#!/bin/bash
set -e
export PYTHONPATH="/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
exec python3 -m shareboard "$@"
EOF
    chmod 0755 "${DEST}/usr/bin/shareboard"

    # --- systemd unit + desktop entry ----------------------------------------
    install -m 0644 "${PROJECT_ROOT}/shareboard.service" \
        "${DEST}/etc/systemd/system/shareboard.service"
    install -m 0644 "${PROJECT_ROOT}/shareboard.desktop" \
        "${DEST}/usr/share/applications/shareboard.desktop"

    # --- /var/lib/shareboard directory marker --------------------------------
    # Keep it owned by root; the runtime creates its own per-board dirs.
    touch "${DEST}/var/lib/shareboard/.keep"

    # --- DEBIAN/control -------------------------------------------------------
    cat > "${DEBIAN_DIR}/control" <<EOF
Package: ${APP_NAME}
Version: ${PKG_VERSION}
Architecture: all
Maintainer: ShareBoard <shareboard@example.invalid>
Depends: python3 (>= 3.10),
         python3-flask (>= 3.0),
         python3-flask-socketio (>= 5.3),
         python3-socketio (>= 5.11),
         python3-bidict,
         python3-h11,
         python3-wsproto,
         python3-simple-websocket
Recommends: python3-pycrdt
Section: web
Priority: optional
Description: Real-time collaborative text editor (Google-Docs style)
 ShareBoard is a Google-Docs-style real-time collaborative editor. Each
 board is a CRDT document (Yjs via pycrdt on the server, y-prosemirror
 in the browser). Multiple users can edit the same board simultaneously
 with presence (remote cursors and names). All edits are persisted to a
 local SQLite database and survive server restarts.
EOF

    # --- DEBIAN/conffiles -----------------------------------------------------
    # Mark the systemd unit as a config file so `dpkg` doesn't clobber
    # local edits on upgrade.
    cat > "${DEBIAN_DIR}/conffiles" <<EOF
/etc/systemd/system/shareboard.service
EOF

    # --- DEBIAN/postinst ------------------------------------------------------
    cat > "${DEBIAN_DIR}/postinst" <<'EOF'
#!/bin/bash
set -e
# pycrdt has no Debian package as of bookworm; pull it from PyPI on first
# install. Idempotent — skip if already present.
if ! python3 -c "import pycrdt" >/dev/null 2>&1; then
    if command -v pip3 >/dev/null 2>&1; then
        pip3 install --break-system-packages --quiet pycrdt || \
            echo "warning: could not auto-install pycrdt; install it manually" >&2
    fi
fi
mkdir -p /var/lib/shareboard
chmod 755 /var/lib/shareboard
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi
exit 0
EOF
    chmod 0755 "${DEBIAN_DIR}/postinst"

    # --- DEBIAN/prerm ---------------------------------------------------------
    cat > "${DEBIAN_DIR}/prerm" <<'EOF'
#!/bin/bash
set -e
if [ -d /run/systemd/system ]; then
    systemctl stop shareboard >/dev/null 2>&1 || true
    systemctl disable shareboard >/dev/null 2>&1 || true
fi
exit 0
EOF
    chmod 0755 "${DEBIAN_DIR}/prerm"

    # --- Build it -------------------------------------------------------------
    OUT="$(dirname "${PROJECT_ROOT}")/${APP_NAME}_${PKG_VERSION}_all.deb"
    dpkg-deb --build --root-owner-group --uniform-compression \
        "${DEST}" "${OUT}"

    echo
    echo "Built: ${OUT}"
    echo
    echo "Inspect it:"
    echo "  dpkg-deb -I ${OUT} | head"
    echo "  dpkg-deb -c ${OUT} | head -20"
    echo
    echo "Install with:"
    echo "  sudo apt install ./$(basename "${OUT}")"
fi
