#!/usr/bin/env bash

# Run one local SSiSLS FACTS scenario and show task-based progress.
# Execute this script from Ubuntu/WSL after Docker Desktop is running.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:-ssp245}"
FACTS_REPO="${FACTS_REPO:-$HOME/facts_ssisls/facts}"
FACTS_IMAGE="${FACTS_IMAGE:-ssisls}"
CPU_COUNT="${CPU_COUNT:-8}"
MEMORY_LIMIT="${MEMORY_LIMIT:-12g}"
EXPECTED_TASKS="${EXPECTED_TASKS:-auto}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$SCRIPT_DIR/outputs}"
RUN_LOG="${RUN_LOG:-$SCRIPT_DIR/FACTS_RUN_${SCENARIO}.log}"
PROGRESS_FILE="${PROGRESS_FILE:-$SCRIPT_DIR/FACTS_STATUS.txt}"

case "$SCENARIO" in
  ssp119|ssp126|ssp245|ssp370|ssp585) ;;
  *)
    echo "Usage: $0 [ssp119|ssp126|ssp245|ssp370|ssp585]" >&2
    exit 2
    ;;
esac

if [[ "$EXPECTED_TASKS" != "auto" ]] && ! [[ "$EXPECTED_TASKS" =~ ^[1-9][0-9]*$ ]]; then
  echo "EXPECTED_TASKS must be 'auto' or a positive integer." >&2
  exit 2
fi

cd "$FACTS_REPO"
docker info >/dev/null
docker image inspect "$FACTS_IMAGE" >/dev/null

LOCATION_FILE="experiments_ssisls/$SCENARIO/location.lst"
CONFIG_FILE="experiments_ssisls/$SCENARIO/config.yml"
EXPERIMENT_DIR="experiments_ssisls/$SCENARIO"
if grep -q 'pipeline\.global\.yml' "$CONFIG_FILE"; then
  run_scale="global"
  site_count=0
  target_label="global mean"
else
  run_scale="local"
  if [[ ! -s "$LOCATION_FILE" ]]; then
    echo "Missing or empty location file: $LOCATION_FILE" >&2
    exit 1
  fi
  site_count="$(awk 'NF { count++ } END { print count+0 }' "$LOCATION_FILE")"
  if [[ "$site_count" -lt 1 ]]; then
    echo "No locations found in $LOCATION_FILE." >&2
    exit 1
  fi
  target_label="$site_count location(s)"
fi

missing=0
while IFS= read -r url; do
  name="${url##*/}"
  if [[ ! -s "modules-data/$name" ]]; then
    echo "Missing module data: $name" >&2
    missing=1
  fi
done < modules-data/modules-data.urls.txt
if [[ "$missing" -ne 0 ]]; then
  echo "Resume the official downloads before running FACTS:" >&2
  echo "  cd $FACTS_REPO/modules-data" >&2
  echo "  wget --continue --input-file=modules-data.urls.txt" >&2
  exit 1
fi

mkdir -p _scratch "$OUTPUT_ROOT/$SCENARIO"
chmod 0777 _scratch

# The WSL checkout is normally owned by UID 1000, while the ssisls image runs
# FACTS as jovyan (UID 1001). Prepare only this generated experiment directory
# so the container can create workflows.yml, logs, and NetCDF output. Remove the
# generated workflows file because it is always rebuilt from the current config.
docker run --rm --user 0 \
  --volume "$FACTS_REPO:/opt/facts" \
  --workdir /opt/facts \
  "$FACTS_IMAGE" \
  bash -lc "mkdir -p '$EXPERIMENT_DIR/output' && chmod 0777 '$EXPERIMENT_DIR' '$EXPERIMENT_DIR/output' && rm -f '$EXPERIMENT_DIR/workflows.yml'"

if [[ "$EXPECTED_TASKS" == "auto" ]]; then
  echo "Inspecting the selected FACTS workflows..."
  set +e
  preflight_output="$(docker run --rm \
    --volume "$FACTS_REPO:/opt/facts" \
    --volume "$FACTS_REPO/modules-data:/opt/facts/modules-data" \
    --workdir /opt/facts \
    "$FACTS_IMAGE" \
    bash -lc "python runFACTS.py 'experiments_ssisls/$SCENARIO' --debug" 2>&1)"
  preflight_status=$?
  set -e
  if [[ "$preflight_status" -ne 0 ]]; then
    printf '%s\n' "$preflight_output" >&2
    echo "FACTS could not parse the selected-workflow configuration." >&2
    exit "$preflight_status"
  fi
  EXPECTED_TASKS="$(printf '%s\n' "$preflight_output" | grep -c '^Task ' || true)"
  selected_workflows="$(printf '%s\n' "$preflight_output" | sed -n -E 's/^WORKFLOW:[[:space:]]*//p' | paste -sd, -)"
  if ! [[ "$EXPECTED_TASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Could not determine the task count from FACTS debug output." >&2
    exit 1
  fi
  echo "Selected workflows: ${selected_workflows:-unknown}"
  echo "Generated pipeline: $EXPECTED_TASKS task(s)."
fi

make_bar() {
  local percent="$1"
  local width=30
  local filled=$((percent * width / 100))
  local empty=$((width - filled))
  local left right
  printf -v left '%*s' "$filled" ''
  printf -v right '%*s' "$empty" ''
  left="${left// /#}"
  right="${right// /-}"
  BAR_TEXT="[$left$right]"
}

show_progress() {
  local completed="$1"
  local percent="$2"
  local stage="$3"
  make_bar "$percent"
  printf -v progress_line '%s %3d%%  tasks %d/%d  %s' \
    "$BAR_TEXT" "$percent" "$completed" "$EXPECTED_TASKS" "$stage"
  printf '%s  RUNNING %s for %s  %s\n' \
    "$(date --iso-8601=seconds)" "$SCENARIO" "$target_label" "$progress_line" \
    > "$PROGRESS_FILE"
  if [[ -t 1 ]]; then
    printf '\r\033[K%s' "$progress_line"
  else
    printf '%s\n' "$progress_line"
  fi
}

container_name="facts_${SCENARIO}_$(date +%Y%m%d_%H%M%S)"
: > "$RUN_LOG"
exec 3>>"$RUN_LOG"

echo "Starting $run_scale FACTS $SCENARIO for $target_label."
echo "Detailed log: $RUN_LOG"
echo "The percentage is an estimate based on $EXPECTED_TASKS tasks from the calibrated pipeline."
show_progress 0 0 "Starting container"

declare -A completed_tasks=()
done_pattern='^Update: (.+\.task[0-9]+) state: DONE'
failed_pattern='^Update: (.+) state: FAILED'

set +e
docker run --rm --init \
  --name "$container_name" \
  --cpus "$CPU_COUNT" \
  --memory "$MEMORY_LIMIT" \
  --memory-swap "$MEMORY_LIMIT" \
  -e HDF5_USE_FILE_LOCKING=FALSE \
  --volume "$FACTS_REPO:/opt/facts" \
  --volume "$FACTS_REPO/modules-data:/opt/facts/modules-data" \
  --volume "$FACTS_REPO/_scratch:/home/jovyan/radical.pilot.sandbox" \
  --workdir /opt/facts \
  "$FACTS_IMAGE" \
  bash -lc "bash submit_ssisls_experiment.sh '$SCENARIO'" 2>&1 |
sed -u -E 's/\x1B\[[0-9;]*[mK]//g' |
while IFS= read -r line; do
  printf '%s\n' "$line" >&3
  if [[ "$line" =~ $done_pattern ]]; then
    task_id="${BASH_REMATCH[1]}"
    if [[ -z "${completed_tasks[$task_id]+x}" ]]; then
      completed_tasks["$task_id"]=1
      completed="${#completed_tasks[@]}"
      percent=$((completed * 98 / EXPECTED_TASKS))
      if [[ "$percent" -gt 98 ]]; then
        percent=98
      fi
      short_task="${task_id#${SCENARIO}.}"
      show_progress "$completed" "$percent" "Completed: $short_task"
    fi
  elif [[ "$line" =~ $failed_pattern ]]; then
    printf '\n%s\n' "FACTS reported a failed task: ${BASH_REMATCH[1]}" >&2
  fi
done
docker_status="${PIPESTATUS[0]}"
set -e
exec 3>&-

if [[ "$docker_status" -ne 0 ]]; then
  [[ -t 1 ]] && printf '\n'
  printf '%s  FAILED %s (exit code %d); see %s\n' \
    "$(date --iso-8601=seconds)" "$SCENARIO" "$docker_status" "$RUN_LOG" \
    > "$PROGRESS_FILE"
  echo "FACTS failed with exit code $docker_status. See: $RUN_LOG" >&2
  exit "$docker_status"
fi

show_progress "$EXPECTED_TASKS" 99 "Copying NetCDF outputs"
find "experiments_ssisls/$SCENARIO/output" -maxdepth 1 -type f -name '*.nc' \
  -exec cp -a -t "$OUTPUT_ROOT/$SCENARIO" {} +

make_bar 100
printf -v final_line '%s 100%%  tasks %d/%d  Complete' \
  "$BAR_TEXT" "$EXPECTED_TASKS" "$EXPECTED_TASKS"
if [[ -t 1 ]]; then
  printf '\r\033[K%s\n' "$final_line"
else
  printf '%s\n' "$final_line"
fi
printf '%s  COMPLETE %s for %s; outputs: %s\n' \
  "$(date --iso-8601=seconds)" "$SCENARIO" "$target_label" \
  "$OUTPUT_ROOT/$SCENARIO" > "$PROGRESS_FILE"

echo "FACTS $SCENARIO complete. NetCDF outputs copied to:"
echo "  $OUTPUT_ROOT/$SCENARIO"
