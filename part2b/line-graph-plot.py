from pathlib import Path
import re

import matplotlib.pyplot as plt

THREADS = [1, 2, 4, 8]
BENCHMARK_LINE_RE = re.compile(r"^\s*(parsec-[\w-]+):")
SPEEDUPS_LINE_RE = re.compile(r"^\s*Speedups vs 1 threads:\s*(.+)$")
PAIR_RE = re.compile(r"(\d+)t=([0-9]*\.?[0-9]+)x")


def parse_results_file(results_file: Path) -> dict[str, dict[int, float]]:
    """
    Parse benchmark speedups from a results.txt file.

    Returns:
        {
            "parsec-barnes": {1: 1.0, 2: 1.99, 4: 3.92, 8: 5.08},
            ...
        }
    """
    benchmark_speedups: dict[str, dict[int, float]] = {}
    current_benchmark: str | None = None

    with results_file.open("r", encoding="utf-8") as f:
        for line in f:
            bench_match = BENCHMARK_LINE_RE.match(line)
            if bench_match:
                current_benchmark = bench_match.group(1)
                continue

            speedups_match = SPEEDUPS_LINE_RE.match(line)
            if speedups_match and current_benchmark:
                pairs_text = speedups_match.group(1)
                values: dict[int, float] = {}
                for t_str, s_str in PAIR_RE.findall(pairs_text):
                    values[int(t_str)] = float(s_str)

                if values:
                    benchmark_speedups[current_benchmark] = values

    return benchmark_speedups


def plot_speedups(timestamp_dir: Path, benchmark_speedups: dict[str, dict[int, float]]) -> None:
    if not benchmark_speedups:
        print(f"Warning: No benchmark speedup data found in {timestamp_dir / 'results.txt'}")
        return

    plt.figure(figsize=(10, 6))

    for benchmark, speedup_map in sorted(benchmark_speedups.items()):
        y_values = [speedup_map.get(t, float("nan")) for t in THREADS]
        label = benchmark.replace("parsec-", "")
        plt.plot(THREADS, y_values, marker="o", linewidth=2, label=label)

    plt.title("Thread Speedup Scaling")
    plt.xlabel("Number of Threads")
    plt.ylabel("Speedup")
    plt.xscale("linear")
    plt.xticks(THREADS)
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend()
    plt.tight_layout()

    output_file = timestamp_dir / "speedup_plot.png"
    plt.savefig(output_file, dpi=150)
    plt.close()
    print(f"Saved plot: {output_file}")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir / "data"

    if not data_dir.exists() or not data_dir.is_dir():
        print(f"Warning: data directory does not exist: {data_dir}")
        return

    timestamp_dirs = sorted([p for p in data_dir.iterdir() if p.is_dir()])

    if not timestamp_dirs:
        print(f"Warning: No timestamp folders found in {data_dir}")
        return

    for timestamp_dir in timestamp_dirs:
        results_file = timestamp_dir / "results.txt"
        if not results_file.exists():
            print(f"Warning: Missing results file: {results_file}")
            continue

        benchmark_speedups = parse_results_file(results_file)
        plot_speedups(timestamp_dir, benchmark_speedups)


if __name__ == "__main__":
    main()