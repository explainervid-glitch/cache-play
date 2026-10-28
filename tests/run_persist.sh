#!/bin/bash
# Runs the kept-cache phases of test_persist.py in order, each in a new Blender.
#   bash tests/run_persist.sh <blender.exe> <work_dir>
# Prints one line per check and leaves the reports in <work_dir>.
BLENDER="$1"
WORK="$2"
ROOT="$(cd "$(dirname "$0")/.." && pwd -W 2>/dev/null || pwd)"
rm -rf "$WORK"; mkdir -p "$WORK/cache"
BLEND="$WORK/persist.blend"
run() {
  local phase="$1"; shift
  timeout 180 "$BLENDER" --factory-startup --enable-event-simulate "$@" \
    --python "$ROOT/tests/test_persist.py" -- "$ROOT" "$WORK/$phase.json" "$phase" "$BLEND" "$WORK/cache" \
    > "$WORK/$phase.log" 2>&1
}
run make
run reopen "$BLEND"
run reopen_unsaved "$BLEND"
run edit_save "$BLEND"
run after_save "$BLEND"
touch "$BLEND"   # changed outside Blender: a new date
run touched "$BLEND"
run save_as "$BLEND"
run reopen_as "${BLEND%.blend}_as.blend"
run clear_all "${BLEND%.blend}_as.blend"
for phase in make reopen reopen_unsaved edit_save after_save touched save_as reopen_as clear_all; do
  if [ -f "$WORK/$phase.json" ]; then
    grep -E '"test"|"ok"' "$WORK/$phase.json" | paste - - | sed 's/  */ /g'
  else
    echo "$phase: no report (see $WORK/$phase.log)"
  fi
done
