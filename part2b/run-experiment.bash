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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREDNS_PATCH_FILE="$SCRIPT_DIR/../coredns-topology-patch.json"

ensure_coredns_single_replica() {
    echo "Ensuring CoreDNS is schedulable (replicas=1)..."

    # Wait until coredns deployment appears after control plane update.
    for _ in {1..30}; do
        if "$KUBECTL_BIN" -n kube-system get deployment coredns >/dev/null 2>&1; then
            if [[ -f "$COREDNS_PATCH_FILE" ]]; then
                coredns_patch_json="$(cat "$COREDNS_PATCH_FILE")"
                "$KUBECTL_BIN" -n kube-system patch deployment coredns --type merge --patch "$coredns_patch_json" >/dev/null 2>&1 || true
            else
                echo "Warning: CoreDNS patch file not found at $COREDNS_PATCH_FILE" >&2
            fi

            "$KUBECTL_BIN" -n kube-system scale deployment coredns --replicas=1 >/dev/null 2>&1 || true
            "$KUBECTL_BIN" -n kube-system rollout restart deployment/coredns >/dev/null 2>&1 || true

            # Clean up old unschedulable CoreDNS pods after patching topology rules.
            "$KUBECTL_BIN" -n kube-system delete pod -l k8s-app=kube-dns --field-selector=status.phase=Pending --ignore-not-found=true >/dev/null 2>&1 || true
            "$KUBECTL_BIN" -n kube-system rollout status deployment/coredns --timeout=180s >/dev/null 2>&1 || true
            return 0
        fi
        sleep 5
    done

    echo "Warning: coredns deployment not found yet; continuing." >&2
}

# kops setup code
export KOPS_STATE_STORE="gs://cca-eth-2026-group-006-ntroxler"
PROJECT="cca-eth-2026-group-006"

# Check if cluster is already running
if ! "$KOPS_BIN" get cluster --name part2b.k8s.local >/dev/null 2>&1; then
    echo "No existing cluster found. Proceeding with setup."

    "$KOPS_BIN" create -f "$(to_win_path "$SCRIPT_DIR/../part2b.yaml")"
    "$KOPS_BIN" update cluster --name part2b.k8s.local --yes --admin
    ensure_coredns_single_replica
    if ! "$KOPS_BIN" validate cluster --name part2b.k8s.local --wait 20m; then
        echo "Cluster validation failed. Stopping script." >&2
        exit 1
    fi
    "$KUBECTL_BIN" get nodes -o wide
else
    echo "Cluster part2b.k8s.local already exists. will continue with existing setup."
    "$KOPS_BIN" update cluster --name part2b.k8s.local --yes --admin
    ensure_coredns_single_replica
    if ! "$KOPS_BIN" validate cluster --name part2b.k8s.local --wait 20m; then
        echo "Cluster validation failed. Stopping script." >&2
        exit 1
    fi
fi

# Extract parsec server node by its name, then set the label we need later.
parsec_server_name="$("$KUBECTL_BIN" get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^parsec-server-' | head -n1 || true)"

if [[ -z "$parsec_server_name" ]]; then
    echo "Could not find parsec server node (expected a node named parsec-server-*)." >&2
  exit 1
fi

"$KUBECTL_BIN" label node "$parsec_server_name" cca-project-nodetype=parsec


BENCHMARK_DIR="$SCRIPT_DIR/../parsec-benchmarks/part2b"
RUN_TS="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
DATA_DIR="$SCRIPT_DIR/data/$RUN_TS"
N_THREADS=(1 2 4 8)

mkdir -p "$DATA_DIR"
echo "Successfully created data directory: $DATA_DIR"

sanitize() {
    printf '%s' "$1" | sed 's/[^A-Za-z0-9._-]/_/g'
}

readarray -d '' benchmarks < <(find "$BENCHMARK_DIR" -maxdepth 1 -type f -name "*.yaml" -print0)

if [[ ${#benchmarks[@]} -eq 0 ]]; then
    echo "No benchmark YAML files found in $BENCHMARK_DIR" >&2
    exit 1
fi

cleanup() {
    echo "Cleaning up resources..."
    for benchmark_file in "${benchmarks[@]}"; do
        "$KUBECTL_BIN" delete -f "$(to_win_path "$benchmark_file")" --ignore-not-found --wait=true > /dev/null 2>&1 || true
    done
}

trap cleanup EXIT INT TERM

for benchmark_file in "${benchmarks[@]}"; do
    benchmark_name="$(basename "$benchmark_file" .yaml)"
    sanitized_benchmark_name="$(sanitize "$benchmark_name")"

    for n_thread in "${N_THREADS[@]}"; do
        echo "Running benchmark: $benchmark_name with $n_thread threads"

        temp_manifest="$SCRIPT_DIR/.tmp-${benchmark_name}-${n_thread}-threads.yaml"
        sed "s/\${N_THREADS}/${n_thread}/g" "$benchmark_file" > "$temp_manifest"

        job_ref="$("$KUBECTL_BIN" apply -f "$(to_win_path "$temp_manifest")" -o name | grep '^job' | head -n1 || true)"
        if [[ -z "$job_ref" ]]; then
            echo "No Job resource found in $benchmark_file for thread count $n_thread" >&2
            rm -f "$temp_manifest"
            continue
        fi

        # Wait for the job to complete
        if ! "$KUBECTL_BIN" wait --for=condition=complete --timeout=30m "$job_ref" > /dev/null 2>&1; then
            echo "Benchmark $benchmark_name with $n_thread threads did not complete successfully within the timeout." >&2
            "$KUBECTL_BIN" delete "$job_ref" --ignore-not-found --wait=true >/dev/null 2>&1 || true
            rm -f "$temp_manifest"
            continue
        fi

        # Get logs from the job's pod
        pod_name="$("$KUBECTL_BIN" get pods --selector=job-name="$benchmark_name" -o jsonpath='{.items[0].metadata.name}')"
        if [[ -z "$pod_name" ]]; then
            echo "Could not find pod for benchmark $benchmark_name with $n_thread threads." >&2
            continue
        fi

        logs="$("$KUBECTL_BIN" logs "$pod_name")"
        output_file="$DATA_DIR/${sanitized_benchmark_name}_${n_thread}_threads.txt"
        printf '%s\n' "$logs" > "$output_file"
        echo "Saved logs to $output_file"

        "$KUBECTL_BIN" delete "$job_ref" --ignore-not-found --wait=true >/dev/null 2>&1 || true
        rm -f "$temp_manifest"
    done
done


# Remove cluster in the end
"$KOPS_BIN" delete cluster part2b.k8s.local --yes