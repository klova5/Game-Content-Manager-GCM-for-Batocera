#!/bin/bash
# Runs GCM's offline fixtures. Needs python3 (and g++ for the frontend test). Writes only to a temp folder.
set -e
cd "$(dirname "$0")/.."
SYS=system; PERM=$SYS/add-ons/game-content-manager/v36-permanent
python3 -B tests/test_supervisor_shutdown.py "$PERM/gcm-v36-permanent-supervisor.py"
python3 -B tests/test_supervisor_v44.py "$PERM/gcm-v36-permanent-supervisor.py"
python3 -B tests/test_boot_worker.py "$PERM/gcm-v36-boot-wait.py"
python3 -B tests/test_citron_update_resolution.py "$SYS/gcm-native-test/step60-shared-switch/citron-update-resolution.py"
python3 -B tests/test_ps4_summary.py "$SYS/gcm-native-test/step61-common-content/gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py"
if command -v g++ >/dev/null; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  sed -n '/^\/\/ 61TJ: while a game/,/^static std::shared_ptr<TextComponent> gcmV78LiveSummary/p' emulationstation/es-app/src/guis/GuiGcmNative.h > "$T/live_summary.inc"
  sed -n '/^static std::shared_ptr<TextComponent> gcmV78LiveSummary/,/^}/p' emulationstation/es-app/src/guis/GuiGcmNative.h | tail -n +2 >> "$T/live_summary.inc"
  g++ -std=c++17 -Wall -Wextra -Werror -pthread -I"$T" tests/test_live_summary.cpp -o "$T/live" && "$T/live"
fi
echo "ALL GCM TESTS PASS"
