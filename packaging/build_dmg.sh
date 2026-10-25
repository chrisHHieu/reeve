#!/usr/bin/env bash
# The macOS app (Electron shell) and a .dmg to drag it into Applications.
#
#   bash packaging/build_dmg.sh
#
# Steps:
#   1. PyInstaller freezes the server into one folder (onedir), so no venv is needed at run time.
#   2. That folder is staged at surfaces/gui/binaries/sidecar/ for electron-builder's
#      `extraResources`, with a portable Node, ripgrep, and every Mach-O signed.
#   3. The dictation host (the stt crate) is built, then electron-builder `--mac zip` makes
#      Reeve.app, its .zip and latest-mac.yml (what the updater reads).
#   4. hdiutil wraps the .app in a compressed .dmg with a styled install window.
#   5. With a signing identity: the .dmg is signed, notarized and stapled.
#
# Needs (as build_windows.ps1 does on Windows): Rust (rustup), Node and npm with `npm ci` done in
# surfaces/gui, and a venv at .venv in the repository with the package installed editable and the
# build-only tools:
#     python3 -m venv .venv
#     .venv/bin/pip install -e ".[dev,messaging,bedrock]" pyinstaller tzdata typer
# typer only matters while freezing: PyInstaller imports `mcp.cli`, which calls sys.exit() when
# typer is missing and stops the build. (aisuite is an ordinary dependency, pinned to a git
# commit in pyproject.toml.)
#
# Signing: APPLE_SIGNING_IDENTITY="Developer ID Application: … (TEAMID)" signs the .app and the
# sidecar (hardened runtime, surfaces/gui/electron/entitlements.plist). Without it the build is
# unsigned and its first launch needs right-click → Open.
#
# Notarizing (step 5, only with an identity): a signature alone is not enough for a download,
# which macOS meets with "Apple could not verify… Move to Trash?". It takes an App Store Connect
# API key in NOTARYTOOL_API_KEY_PATH / NOTARYTOOL_API_KEY_ID / NOTARYTOOL_API_ISSUER_ID, from the
# environment, from $REEVE_NOTARY_ENV, or from `.reeve-notary.env` in the folder ABOVE the
# repository (one file for every clone and worktree on the machine, never committed). Without
# them the .dmg is still made, with a warning that cannot be missed.
#
# For trying things locally: no APPLE_SIGNING_IDENTITY gives an unsigned build, and
# REEVE_SKIP_NOTARIZE=1 signs but skips the notary's slow round trip. Distribute neither.
#
# Experimental connectors are left out (the spec drops harness.integrations.connectors.
# experimental); a build of your own can keep them: REEVE_EXPERIMENTAL=1 bash build_dmg.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
GUI="$PLATFORM/surfaces/gui"
SIDECAR="$GUI/binaries/sidecar"
APP="Reeve"
# The version lives in surfaces/gui/package.json and nowhere else; it names the .dmg too.
VERSION="$(node -p "require('$GUI/package.json').version")"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"   # e.g. aarch64-apple-darwin
ARCH="${TRIPLE%%-*}"

say() { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
signing() { [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; }

# On a CI runner the Developer ID certificate exists only as the APPLE_CERTIFICATE secret (a .p12
# in base64). It goes into a keychain made for this build, so the codesign calls below find the
# identity (without this: "no identity found", run 29773913622 of v0.1.3). electron-builder signs
# with it later; this is for the signing that comes before. A local build has the identity in the
# login keychain already and never sets APPLE_CERTIFICATE.
import_ci_certificate() {
  [ -n "${APPLE_CERTIFICATE:-}" ] && signing || return 0
  say "importing signing certificate into a temporary keychain"
  local dir keychain password
  dir="$(mktemp -d)"
  keychain="$dir/reeve-signing.keychain-db"
  password="$(openssl rand -hex 16)"
  security create-keychain -p "$password" "$keychain"
  security set-keychain-settings -lut 21600 "$keychain"
  security unlock-keychain -p "$password" "$keychain"
  echo "$APPLE_CERTIFICATE" | base64 -d > "$dir/cert.p12"
  security import "$dir/cert.p12" -P "${APPLE_CERTIFICATE_PASSWORD:-}" -A -t cert -f pkcs12 -k "$keychain"
  rm -f "$dir/cert.p12"
  # A runner has no screen to ask on, so codesign must be allowed to use the key without one.
  security set-key-partition-list -S "apple-tool:,apple:" -s -k "$password" "$keychain" >/dev/null
  security list-keychains -d user -s "$keychain" login.keychain-db
}

freeze_server() {
  say "[1/5] PyInstaller: bundling reeve-server ($TRIPLE)"
  "$PLATFORM/.venv/bin/pyinstaller" --noconfirm --clean --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/reeve-server.spec"
}

# The onedir bundle (the executable and _internal/) ships as `extraResources` at
# Contents/Resources/sidecar/; onefile unpacked itself on every launch and cost 6-7 s of splash.
stage_sidecar() {
  say "[2/5] staging sidecar resources"
  mkdir -p "$GUI/binaries"
  # Removed first because cp writes THROUGH a symlink already at the target (a convenience link in
  # the old externalBin slot once overwrote another worktree's venv script, 2026-07-11); this also
  # clears an old onefile binary.
  rm -rf "$SIDECAR" "$GUI/binaries/reeve-server-$TRIPLE"
  # -L follows links. A bundler that flattens symlinks makes real copies, so Python.framework's
  # links (Python -> Versions/Current/Python, …) arrived in the .app as loose copies whose framework
  # signatures fail outside the bundle: the notary refused them twice (f73463f3, ca30027a,
  # 2026-07-16). Following them here makes what is signed exactly what the bundler copies, and each
  # Mach-O gets a plain signature of its own.
  cp -RL "$HERE/dist/reeve-server" "$SIDECAR"
  [ -z "$(find "$SIDECAR" -type l | head -1)" ] || fail "symlinks survived sidecar staging — the bundler would flatten them into unsigned copies"
}

# The browser MCP server (@playwright/mcp) is a Node program, and a new Mac has no Node, so the app
# brings a portable one with the package installed, where builtin_mcp_servers() looks beside the
# sidecar binary:
#   sidecar/node/bin/node                              (the runtime)
#   sidecar/mcp/node_modules/@playwright/mcp/cli.js    (the server, installed here)
# The signing pass below signs this node like every other Mach-O.
add_node() {
  local version="22.14.0" arch archive
  arch="$(uname -m)"
  [ "$arch" = "arm64" ] || arch="x64"
  mkdir -p "$SIDECAR/node/bin" "$SIDECAR/mcp"
  echo "    downloading node v$version (portable, macos $arch)"
  archive="$(mktemp -d)/node.tar.gz"
  curl -fsSL "https://nodejs.org/dist/v$version/node-v$version-darwin-$arch.tar.gz" -o "$archive"
  tar -xzf "$archive" -C "$(dirname "$archive")"
  cp "$(dirname "$archive")/node-v$version-darwin-$arch/bin/node" "$SIDECAR/node/bin/node"
  npm install --prefix "$SIDECAR/mcp" "@playwright/mcp@latest" --no-audit --no-fund
}

# `grep` and `list_files` run ripgrep, which macOS does not have; without it they walk in Python
# (12.7 s against 0.2 s over this repository, 2026-10-11). A pinned build goes where
# grep_engines.carried_rg() looks, sidecar/rg/rg with its licence files, after its archive matches
# the hash the release publishes. The signing pass signs it.
add_ripgrep() {
  local version="15.2.0" triple expected name tmp got says
  if [ "$(uname -m)" = "arm64" ]; then
    triple="aarch64-apple-darwin"
    expected="3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4"
  else
    triple="x86_64-apple-darwin"
    expected="af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1"
  fi
  name="ripgrep-$version-$triple"
  mkdir -p "$SIDECAR/rg"
  echo "    downloading ripgrep v$version ($triple)"
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/BurntSushi/ripgrep/releases/download/$version/$name.tar.gz" -o "$tmp/rg.tar.gz"
  got="$(shasum -a 256 "$tmp/rg.tar.gz" | cut -d' ' -f1)"
  [ "$got" = "$expected" ] || fail "ripgrep archive hash mismatch: got $got, expected $expected"
  tar -xzf "$tmp/rg.tar.gz" -C "$tmp"
  local file
  for file in rg LICENSE-MIT UNLICENSE COPYING; do
    cp "$tmp/$name/$file" "$SIDECAR/rg/$file"
  done
  rm -rf "$tmp"
  chmod +x "$SIDECAR/rg/rg"
  says="$("$SIDECAR/rg/rg" --version)"
  echo "    -> ${says%%$'\n'*}"
}

# Once links are followed, Python.framework is a second copy of _internal/Python (the one the
# PyInstaller bootloader loads; the sidecar runs without the framework) plus an Info.plist. Any file
# under a *.framework/ path makes codesign and the notary treat it as a bundle, which this flat
# layout can never pass: three Invalid verdicts (f73463f3, ca30027a and one more) until it went. No
# .framework may ship in the sidecar.
drop_framework() {
  rm -rf "$SIDECAR/_internal/Python.framework"
  [ -z "$(find "$SIDECAR" -type d -name "*.framework" | head -1)" ] || fail "a .framework appeared in the sidecar — it cannot pass notarization in this layout"
  chmod +x "$SIDECAR/reeve-server"
}

# Before electron-builder: every Mach-O in the sidecar gets a plain file signature that stands on
# its own (hardened runtime, timestamp, the one identity), which is what the notary checks of a
# flattened tree. Only the entry point carries the entitlements (disable-library-validation: the
# bundled python.org libraries are signed by another team). electron-builder then seals the
# resources into the .app's signature.
sign_sidecar() {
  signing || return 0
  echo "    signing sidecar binaries"
  find "$SIDECAR" -type f ! -name "reeve-server" ! -name "*.py" ! -name "*.pyc" ! -name "*.txt" ! -name "*.pem" ! -name "*.json" -print0 |
    while IFS= read -r -d '' file; do
      file -b "$file" | grep -q "Mach-O" || continue
      codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime "$file"
    done
  codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime --entitlements "$GUI/electron/entitlements.plist" "$SIDECAR/reeve-server"
}

build_app() {
  say "[3/5] the dictation host, the app, and Reeve.app (electron-builder)"
  (cd "$PLATFORM/stt" && cargo build --release --bin reeve-stt-host)
  cp "$PLATFORM/stt/target/release/reeve-stt-host" "$GUI/binaries/reeve-stt-host"
  chmod +x "$GUI/binaries/reeve-stt-host"
  if signing; then
    codesign --force --sign "$APPLE_SIGNING_IDENTITY" --timestamp --options runtime --entitlements "$GUI/electron/entitlements.plist" "$GUI/binaries/reeve-stt-host"
  fi
  # With no identity given, electron-builder is told not to look: it would sign with whatever
  # identity the keychain happens to hold.
  local identity=()
  if signing; then
    identity=("-c.mac.identity=${APPLE_SIGNING_IDENTITY#Developer ID Application: }")
  else
    export CSC_IDENTITY_AUTO_DISCOVERY=false
  fi
  # ${identity[@]+…}: an EMPTY array as "${identity[@]}" is "unbound" under set -u in the bash 3.2
  # macOS ships.
  (cd "$GUI" && npm run build && npm run electron:build && npx electron-builder --mac zip --publish never ${identity[@]+"${identity[@]}"})
}

# The install window: a fixed size, the two icons placed on the background's arrow, instead of
# Finder's large bare window. It needs Finder (AppleScript); without one (CI with no session) the
# caller falls back to a plain compressed image. Two things once left the .dmg unstyled without a
# word: a stale "$APP" volume still mounted made this image mount as "$APP 1" while `tell disk
# "$APP"` styled the stale one, so the stale mount is detached first and the real mounted name is
# used; and Finder writes .DS_Store when it likes, so detaching early lost it, so it is waited for.
style_dmg() {
  [ -d "/Volumes/$APP" ] && hdiutil detach "/Volumes/$APP" -force >/dev/null 2>&1 || true
  local writable mounted device mount volume
  writable="$(mktemp -u).dmg"
  hdiutil create -volname "$APP" -srcfolder "$STAGING" -fs HFS+ -format UDRW -ov "$writable" >/dev/null
  mounted="$(hdiutil attach -readwrite -noverify -noautoopen "$writable")"
  device="$(echo "$mounted" | grep -Eo '^/dev/disk[0-9]+' | head -1)"
  mount="$(echo "$mounted" | grep -Eo '/Volumes/.*$' | head -1)"
  [ -n "$device" ] && [ -n "$mount" ] || return 1
  volume="$(basename "$mount")"
  sleep 1
  # y=190 puts the icons on the arrow, the app to its left and Applications to its right. The
  # background is named by its relative HFS path, so the alias survives a rename; closing and
  # opening again, then update, is what makes Finder write .DS_Store at all.
  osascript <<OSA || { hdiutil detach "$device" -force >/dev/null 2>&1 || true; return 1; }
tell application "Finder"
  tell disk "$volume"
    open
    delay 1
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 840, 543}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 96
    set text size of opts to 12
    set background picture of opts to file ".background:bg.tiff"
    set position of item "$APP.app" of container window to {172, 190}
    set position of item "Applications" of container window to {468, 190}
    close
    open
    update without registering applications
    delay 3
  end tell
end tell
OSA
  local second
  for second in $(seq 1 15); do
    [ -f "$mount/.DS_Store" ] && break
    sleep 1
  done
  [ -f "$mount/.DS_Store" ] || { hdiutil detach "$device" -force >/dev/null 2>&1 || true; return 1; }
  sync
  sync
  hdiutil detach "$device" -force >/dev/null
  hdiutil convert "$writable" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
  rm -f "$writable"
}

make_dmg() {
  say "[4/5] hdiutil: wrapping into .dmg"
  local release="$GUI/release" app_path
  app_path="$(find "$release" -maxdepth 2 -type d -name "$APP.app" | head -1)"
  [ -n "$app_path" ] || fail "electron-builder wrote no $APP.app under $release"
  STAGING="$(mktemp -d)"
  cp -R "$app_path" "$STAGING/"
  ln -s /Applications "$STAGING/Applications"
  # The arrow and "drag to Applications", in the hidden folder Finder reads the window from. A HiDPI
  # TIFF (1x and 2x) so it stays sharp on Retina, where a 1x PNG is stretched and blurs.
  mkdir "$STAGING/.background"
  cp "$HERE/dmg-background.tiff" "$STAGING/.background/bg.tiff"
  DMG="$release/${APP}_${VERSION}_${ARCH}.dmg"
  mkdir -p "$(dirname "$DMG")"
  rm -f "$DMG"
  if ! style_dmg; then
    echo "    (Finder styling unavailable — writing a plain .dmg)"
    hdiutil create -volname "$APP" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
  fi
  rm -rf "$STAGING"
}

notarize() {
  if ! signing; then
    echo "    (unsigned dev build — set APPLE_SIGNING_IDENTITY for a distributable DMG)"
    return 0
  fi
  if [ "${REEVE_SKIP_NOTARIZE:-}" = "1" ]; then
    # Signing takes seconds, the notary minutes. A .dmg built here carries no quarantine flag, so
    # Gatekeeper never asks on this machine. NEVER hand out a build made this way.
    say "[5/5] REEVE_SKIP_NOTARIZE=1 — signing container, SKIPPING notarize/staple (do not distribute)"
    codesign --sign "$APPLE_SIGNING_IDENTITY" --timestamp "$DMG"
    return 0
  fi
  say "[5/5] release finishing: sign container → notarize → staple"
  codesign --sign "$APPLE_SIGNING_IDENTITY" --timestamp "$DMG"
  # CI passes the App Store Connect key as APPLE_API_* (release.yml); the container uses the same.
  NOTARYTOOL_API_KEY_PATH="${NOTARYTOOL_API_KEY_PATH:-${APPLE_API_KEY_PATH:-}}"
  NOTARYTOOL_API_KEY_ID="${NOTARYTOOL_API_KEY_ID:-${APPLE_API_KEY:-}}"
  NOTARYTOOL_API_ISSUER_ID="${NOTARYTOOL_API_ISSUER_ID:-${APPLE_API_ISSUER:-}}"
  local env_file="${REEVE_NOTARY_ENV:-$PLATFORM/../.reeve-notary.env}"
  if [ -z "${NOTARYTOOL_API_KEY_PATH:-}" ] && [ -f "$env_file" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
  if [ -z "${NOTARYTOOL_API_KEY_PATH:-}" ] || [ -z "${NOTARYTOOL_API_KEY_ID:-}" ] || [ -z "${NOTARYTOOL_API_ISSUER_ID:-}" ]; then
    echo "    WARNING: DMG is signed but NOT notarized — public downloads will see the"
    echo "    'Move to Trash' dialog. Provide NOTARYTOOL_API_KEY_PATH/_KEY_ID/_ISSUER_ID"
    echo "    (env, \$REEVE_NOTARY_ENV, or $env_file)."
    return 0
  fi
  xcrun notarytool submit "$DMG" --key "$NOTARYTOOL_API_KEY_PATH" --key-id "$NOTARYTOOL_API_KEY_ID" --issuer "$NOTARYTOOL_API_ISSUER_ID" --wait
  xcrun stapler staple "$DMG"
  # The check Gatekeeper makes on a download: better the build fails than a .dmg that greets people
  # with the "Move to Trash" malware dialog.
  spctl -a -t open --context context:primary-signature "$DMG"
  echo "    Gatekeeper: accepted (notarized + stapled)"
}

import_ci_certificate
freeze_server
stage_sidecar
add_node
add_ripgrep
drop_framework
sign_sidecar
build_app
make_dmg
notarize
echo ""
echo "Done → $DMG"
