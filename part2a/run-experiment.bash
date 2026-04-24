#!/usr/bin/env bash
set -euo pipefail

resolve_cmd() {
    local cmd_name="$1"
    local resolved_path

    if resolved_path="$(command -v "$cmd_name" 2>/dev/null)"; then
        printf '%s' "$resolved_path"
        return 0
    fi

    if resolved_path="$(command -v "${cmd_name}.exe" 2>/dev/null)"; then
        printf '%s' "$resolved_path"
        return 0
    fi

    echo "Required command not found: $cmd_name" >&2
    exit 1
}

to_win_path() {
    if command -v wslpath >/dev/null 2>&1; then
        wslpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

KOPS_BIN="$(resolve_cmd kops)"
KUBECTL_BIN="$(resolve_cmd kubectl)"

# kops setup code
export KOPS_STATE_STORE="gs://cca-eth-2026-group-006-ntroxler"
PROJECT="cca-eth-2026-group-006"

# Check if cluster is already running
if ! "$KOPS_BIN" get cluster --name part2a.k8s.local >/dev/null 2>&1; then
    echo "No existing cluster found. Proceeding with setup."

    "$KOPS_BIN" create -f "$(to_win_path "$SCRIPT_DIR/../part2a.yaml")"
    "$KOPS_BIN" update cluster --name part2a.k8s.local --yes --admin
    if ! "$KOPS_BIN" validate cluster --name part2a.k8s.local --wait 10m; then
        echo "Cluster validation failed. Stopping script." >&2
        exit 1
    fi
    "$KUBECTL_BIN" get nodes -o wide
else
    echo "Cluster part2a.k8s.local already exists. will continue with existing setup."
    "$KOPS_BIN" update cluster --name part2a.k8s.local --yes --admin
fi

# Extract parsec server node by its name, then set the label we need later.
parsec_server_name="$("$KUBECTL_BIN" get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^parsec-server-' | head -n1 || true)"

if [[ -z "$parsec_server_name" ]]; then
    echo "Could not find parsec server node (expected a node named parsec-server-*)." >&2
  exit 1
fi

# Optional safety check for expected naming style
if [[ ! "$parsec_server_name" =~ ^parsec-server-[a-z0-9-]+$ ]]; then
  echo "Unexpected parsec server node name: $parsec_server_name" >&2
  exit 1
fi

echo "PARSEC server node: $parsec_server_name, setting label \"cca-project-nodetype=parsec\""
"$KUBECTL_BIN" label nodes "$parsec_server_name" cca-project-nodetype=parsec


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERFERENCE_DIR="$SCRIPT_DIR/../interference"
BENCHMARK_DIR="$SCRIPT_DIR/../parsec-benchmarks/part2a"
RUN_TS="$(date +"%Y-%m-%d_%H-%M-%S")"
DATA_DIR="$SCRIPT_DIR/data/$RUN_TS"

mkdir -p "$DATA_DIR"
echo "Successfully created data directory: $DATA_DIR"

sanitize() {
    printf '%s' "$1" | sed 's/[^A-Za-z0-9._-]/_/g'
}

NO_INTERFERENCE_MARKER="__NO_INTERFERENCE__"
NO_INTERFERENCE_NAME="no-interference"

readarray -d '' interference_files < <(
    find "$INTERFERENCE_DIR" -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) -print0
)

if (( ${#interference_files[@]} == 0 )); then
    echo "No interference manifests found in $INTERFERENCE_DIR" >&2
    exit 1
fi

interference_files=("$NO_INTERFERENCE_MARKER" "${interference_files[@]}")

readarray -d '' benchmark_files < <(
    find "$BENCHMARK_DIR" -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) -print0
)

if (( ${#benchmark_files[@]} == 0 )); then
    echo "No benchmark manifests found in $BENCHMARK_DIR" >&2
    exit 1
fi

current_interference_file=""

cleanup() {
    if [[ -n "${current_interference_file:-}" && -f "$current_interference_file" ]]; then
        "$KUBECTL_BIN" delete -f "$(to_win_path "$current_interference_file")" --ignore-not-found --wait=true >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT INT TERM

for interference_file in "${interference_files[@]}"; do
    current_interference_file="$interference_file"
    if [[ "$interference_file" == "$NO_INTERFERENCE_MARKER" ]]; then
        interference_name="$NO_INTERFERENCE_NAME"
        current_interference_file=""
    else
        interference_name="$(sanitize "$(basename "${interference_file%.*}")")"
    fi

    echo "Starting interference: $interference_name"
    if [[ "$interference_file" != "$NO_INTERFERENCE_MARKER" ]]; then
        interference_file_for_kubectl="$(to_win_path "$interference_file")"
        "$KUBECTL_BIN" apply -f "$interference_file_for_kubectl" >/dev/null
        "$KUBECTL_BIN" wait --for=condition=Ready --timeout=600s -f "$interference_file_for_kubectl" >/dev/null
    fi

    for benchmark_file in "${benchmark_files[@]}"; do
        benchmark_name="$(sanitize "$(basename "${benchmark_file%.*}")")"
        benchmark_start_ts="$(date +"%Y-%m-%d_%H-%M-%S")"

        echo "Starting benchmark: $benchmark_name"
        benchmark_file_for_kubectl="$(to_win_path "$benchmark_file")"
        job_ref="$("$KUBECTL_BIN" apply -f "$benchmark_file_for_kubectl" -o name | grep '^job' | head -n1 || true)"

        if [[ -z "$job_ref" ]]; then
            echo "No Job resource found in $benchmark_file" >&2
            exit 1
        fi

        "$KUBECTL_BIN" wait --for=condition=complete --timeout=3600s "$job_ref" >/dev/null

        output_file="$DATA_DIR/${benchmark_name}__${interference_name}__${benchmark_start_ts}.txt"
        "$KUBECTL_BIN" logs "$job_ref" --all-containers=true > "$output_file"

        "$KUBECTL_BIN" delete "$job_ref" --wait=true >/dev/null
    done

    if [[ "$interference_file" != "$NO_INTERFERENCE_MARKER" ]]; then
        "$KUBECTL_BIN" delete -f "$(to_win_path "$interference_file")" --wait=true >/dev/null
        current_interference_file=""
    fi
done


# Remove cluster in the end
"$KOPS_BIN" delete cluster part2a.k8s.local --yes