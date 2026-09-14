#!/bin/bash
# Build a .deb package for ShareBoard.
#
# Usage (from project root):
#   ./build_deb.sh
#   SHAREBOARD_DEB_MODE=manual ./build_deb.sh   # force a path
#   SHAREBOARD_DEB_MODE=proper ./build_deb.sh
#
# Output: ../shareboard_<version>-1_<arch>.deb
#
# This script picks one of two paths:
#
#   1) Manual path (default on Ubuntu 22.04) — dpkg-deb directly, no
#      debhelper, no root. All Python runtime dependencies (flask,
#      flask-socketio, pycrdt, ...) are downloaded from PyPI at *build*
#      time and vendored into /usr/lib/shareboard/site-packages, so the
#      .deb installs and runs on a stock Ubuntu 22.04 (jammy, python
#      3.10) whose apt repos don't carry flask>=3 or pycrdt. Needs pip
#      for python3 (python3-pip, or a .venv built from the same
#      interpreter) plus network access to PyPI at build time only.
#
#   2) Proper path (newer distros only, e.g. Ubuntu 24.04+) —
#      dpkg-buildpackage + pybuild against system Python packages.
#      Requires:
#        sudo apt install dpkg-dev debhelper dh-python \
#                         python3-all python3-setuptools \
#                         python3-wheel pybuild-plugin-pyproject
#      (jammy's dh-python has no pyproject plugin, so this path cannot
#      work there.)

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

MODE="${SHAREBOARD_DEB_MODE:-auto}"

if [ "${MODE}" = "proper" ] \
   || { [ "${MODE}" = "auto" ] \
        && command -v dpkg-buildpackage >/dev/null 2>&1 \
        && command -v dh >/dev/null 2>&1; }; then
    # -------------------------------------------------------------- proper path
    rm -rf "debian/${APP_NAME}"

    cat > debian/changelog <<EOF
${APP_NAME} (${PKG_VERSION}) unstable; urgency=medium

  * Automated build.

 -- ShareBoard <shareboard@example.invalid>  $(date -R)
EOF
    chmod +x debian/rules

    dpkg-buildpackage -us -uc -b
    echo
    echo "Built: ../${APP_NAME}_${PKG_VERSION}_all.deb"
else
    # --------------------------------------------------------------- manual path
    echo "==> manual path: dpkg-deb with vendored Python dependencies"

    if ! command -v dpkg-deb >/dev/null 2>&1; then
        echo "error: dpkg-deb not found. Install it with:" >&2
        echo "  sudo apt install dpkg" >&2
        exit 1
    fi

    # pip for python3: system pip, pip3, or the project's .venv when it
    # was created from the same interpreter (its wheels are identical).
    if python3 -m pip --version >/dev/null 2>&1; then
        PIP_CMD=(python3 -m pip)
    elif command -v pip3 >/dev/null 2>&1; then
        PIP_CMD=(pip3)
    elif [ -x "${PROJECT_ROOT}/.venv/bin/pip" ] \
         && [ "$("${PROJECT_ROOT}/.venv/bin/python" -V 2>/dev/null)" \
              = "$(python3 -V 2>/dev/null)" ]; then
        PIP_CMD=("${PROJECT_ROOT}/.venv/bin/pip")
    else
        echo "error: pip for python3 not found (needed to vendor deps)." >&2
        echo "  sudo apt install python3-pip" >&2
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

    # --- Vendored Python dependencies -----------------------------------------
    # jammy's apt doesn't carry flask>=3 / pycrdt / simple-websocket, so
    # bundle the runtime deps (requirements.txt minus test-only packages)
    # from PyPI into /usr/lib/shareboard/site-packages. The launcher puts
    # that directory on PYTHONPATH ahead of the system paths.
    SITE_PACKAGES="${DEST}/usr/lib/shareboard/site-packages"
    mkdir -p "${SITE_PACKAGES}"
    RUNTIME_REQS="$(mktemp -t shareboard-reqs-XXXXXX)"
    grep -E '^[a-zA-Z0-9]' "${PROJECT_ROOT}/requirements.txt" \
        | grep -vE '^(pytest|pytest-asyncio)([<>=! ]|$)' > "${RUNTIME_REQS}"
    if [ ! -s "${RUNTIME_REQS}" ]; then
        echo "error: failed to parse runtime requirements from requirements.txt" >&2
        rm -f "${RUNTIME_REQS}"
        exit 1
    fi
    echo "==> Vendoring Python dependencies from PyPI:"
    sed 's/^/    /' "${RUNTIME_REQS}"
    "${PIP_CMD[@]}" install --no-input --disable-pip-version-check \
        --no-compile --target "${SITE_PACKAGES}" -r "${RUNTIME_REQS}"
    rm -f "${RUNTIME_REQS}"

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
export PYTHONPATH="/usr/lib/shareboard/site-packages:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
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
    # The vendored wheels are built for this build machine's interpreter
    # (jammy = python3.10), so pin python3 accordingly.
    ARCH="$(dpkg --print-architecture)"
    cat > "${DEBIAN_DIR}/control" <<EOF
Package: ${APP_NAME}
Version: ${PKG_VERSION}
Architecture: ${ARCH}
Maintainer: ShareBoard <shareboard@example.invalid>
Depends: python3 (>= 3.10), python3 (<< 3.11)
Section: web
Priority: optional
Description: Real-time collaborative text editor (Google-Docs style)
 ShareBoard is a Google-Docs-style real-time collaborative editor. Each
 board is a CRDT document (Yjs via pycrdt on the server, y-prosemirror
 in the browser). Multiple users can edit the same board simultaneously
 with presence (remote cursors and names). All edits are persisted to a
 local SQLite database and survive server restarts.
 .
 All Python dependencies (flask, flask-socketio, pycrdt, ...) are
 bundled; the package targets Ubuntu 22.04 (python3 3.10).
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
# Dedicated system user/group for the service (unit's User=shareboard).
if ! id shareboard >/dev/null 2>&1; then
    adduser --system --group --home /var/lib/shareboard \
        --no-create-home shareboard
fi
mkdir -p /var/lib/shareboard
chown shareboard:shareboard /var/lib/shareboard
chmod 755 /var/lib/shareboard
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable shareboard >/dev/null 2>&1 || true
    systemctl restart shareboard >/dev/null 2>&1 || true
fi
exit 0
EOF
    chmod 0755 "${DEBIAN_DIR}/postinst"

    # --- DEBIAN/prerm ---------------------------------------------------------
    cat > "${DEBIAN_DIR}/prerm" <<'EOF'
#!/bin/bash
set -e
# Stop the service on remove/upgrade, but leave it enabled so upgrades
# don't silently disable it.
if [ -d /run/systemd/system ]; then
    systemctl stop shareboard >/dev/null 2>&1 || true
fi
exit 0
EOF
    chmod 0755 "${DEBIAN_DIR}/prerm"

    # --- Normalize permissions ------------------------------------------------
    # The source tree can carry permissive umask bits (e.g. 775/664);
    # system files must be root-owned 755/644.
    find "${DEST}" -type d -exec chmod 0755 {} +
    find "${DEST}" -type f -exec chmod 0644 {} +
    chmod 0755 "${DEBIAN_DIR}/postinst" "${DEBIAN_DIR}/prerm" \
        "${DEST}/usr/bin/shareboard"

    # --- Build it -------------------------------------------------------------
    OUT="$(dirname "${PROJECT_ROOT}")/${APP_NAME}_${PKG_VERSION}_${ARCH}.deb"
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
    echo "  sudo apt install ${OUT}"
fi
