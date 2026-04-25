from __future__ import annotations
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

FILENAME_RE = re.compile(r"^(?P<benchmark>.+)_(?P<threads>\d+)_threads\.txt$", re.IGNORECASE)
NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_filename(path: Path) -> Tuple[str, int] | None:
    m = FILENAME_RE.match(path.name)
    if not m:
        return None
    return m.group("benchmark"), int(m.group("threads"))


def extract_metric_value(path: Path) -> Tuple[float | None, str]:
    """
    Returns (metric_value, direction)
    direction: 'lower_better' (e.g., runtime) or 'higher_better' (e.g., throughput)
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    lower = text.lower()

    # Heuristic for metric direction
    lower_keywords = ("runtime", "time", "latency", "duration", "seconds", "sec", "ms")
    higher_keywords = ("throughput", "ops/s", "op/s", "requests/s", "req/s", "fps", "score")

    has_lower = any(k in lower for k in lower_keywords)
    has_higher = any(k in lower for k in higher_keywords)

    if has_lower and not has_higher:
        direction = "lower_better"
    elif has_higher and not has_lower:
        direction = "higher_better"
    else:
        # fallback
        direction = "lower_better"

    # Try to find explicit "avg runtime/time/throughput" style values first
    explicit_patterns = [
        r"(?i)(?:avg|average)\s*(?:runtime|time|latency|duration)\s*[:=]\s*(" + NUMBER_RE.pattern + r")",
        r"(?i)(?:runtime|time|latency|duration)\s*[:=]\s*(" + NUMBER_RE.pattern + r")",
        r"(?i)(?:avg|average)\s*(?:throughput|score)\s*[:=]\s*(" + NUMBER_RE.pattern + r")",
        r"(?i)(?:throughput|score)\s*[:=]\s*(" + NUMBER_RE.pattern + r")",
    ]

    for p in explicit_patterns:
        m = re.search(p, text)
        if m:
            try:
                return float(m.group(1)), direction
            except ValueError:
                pass

    # Fallback: use median of all numbers in file
    nums = []
    for m in NUMBER_RE.finditer(text):
        try:
            nums.append(float(m.group()))
        except ValueError:
            continue

    if not nums:
        return None, direction

    return statistics.median(nums), direction


def classify_scaling(base_threads: int, points: List[Tuple[int, float]], direction: str) -> Dict[str, object]:
    points = sorted(points, key=lambda x: x[0])
    t0, v0 = points[0]

    def speedup(value: float, threads: int) -> float:
        if direction == "lower_better":
            return v0 / value if value != 0 else float("inf")
        return value / v0 if v0 != 0 else float("inf")

    speedups = [(t, speedup(v, t)) for t, v in points]

    ratios = []
    for t, s in speedups[1:]:
        expected = t / base_threads if base_threads != 0 else 0.0
        if expected > 0:
            ratios.append(s / expected)

    if ratios:
        mean_ratio = sum(ratios) / len(ratios)
    else:
        mean_ratio = 1.0

    if mean_ratio > 1.1:
        scaling = "super-linear"
    elif mean_ratio < 0.8:
        scaling = "sub-linear"
    else:
        scaling = "linear"

    max_threads, max_speedup = max(speedups, key=lambda x: x[0])

    # "Significant" if at least 1.5x speedup at highest thread count
    significant = max_speedup >= 1.5 and max_threads > base_threads

    return {
        "scaling": scaling,
        "significant": significant,
        "speedups": speedups,
        "max_threads": max_threads,
        "max_speedup": max_speedup,
    }


def analyze_timestamp_folder(ts_dir: Path) -> List[str]:
    grouped: Dict[str, List[Tuple[int, float, str]]] = defaultdict(list)
    skipped_files = []

    for txt_file in sorted(ts_dir.glob("*.txt")):
        parsed = parse_filename(txt_file)
        if not parsed:
            skipped_files.append((txt_file.name + "(invalid filename)"))
            continue

        benchmark, threads = parsed
        value, direction = extract_metric_value(txt_file)
        if value is None:
            skipped_files.append((txt_file.name + "(no metric found)"))
            continue

        grouped[benchmark].append((threads, value, direction))

    lines = [f"Timestamp folder: {ts_dir.name}"]

    if not grouped:
        lines.append("  No valid benchmark files found.")
        if skipped_files:
            lines.append(f"  Skipped files:\n    {',\n    '.join(skipped_files)}")
        lines.append("")
        return lines

    for benchmark in sorted(grouped.keys()):
        rows = grouped[benchmark]
        if len(rows) < 2:
            lines.append(f"  {benchmark}: not enough thread points to analyze.")
            continue

        # Choose majority direction (lower_better / higher_better)
        direction = Counter([r[2] for r in rows]).most_common(1)[0][0]
        points = [(t, v) for t, v, _ in rows]
        points = sorted(points, key=lambda x: x[0])

        base_threads = points[0][0]
        result = classify_scaling(base_threads, points, direction)

        sig_text = "yes" if result["significant"] else "no"
        metric_hint = "runtime-like (lower is better)" if direction == "lower_better" else "throughput-like (higher is better)"

        lines.append(
            f"  {benchmark}: {result['scaling']} scaling, significant speedup: {sig_text} "
            f"(max {result['max_speedup']:.2f}x at {result['max_threads']} threads, metric: {metric_hint})"
        )

        detail = ", ".join(f"{t}t={s:.2f}x" for t, s in result["speedups"])
        lines.append(f"    Speedups vs {base_threads} threads: {detail}")

    if skipped_files:
        lines.append(f"  Skipped files: {', '.join(skipped_files)}")

    lines.append("")
    return lines


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir / "data"

    output_lines: List[str] = []

    if not data_dir.exists() or not data_dir.is_dir():
        print(f"Data folder not found: {data_dir}")
        return

    timestamp_dirs = sorted([p for p in data_dir.iterdir() if p.is_dir()], key=lambda p: p.name)

    if not timestamp_dirs:
        output_lines.append("No timestamp folders found in data directory.")
    else:
        output_lines.append("Benchmark thread scaling analysis")
        output_lines.append("=" * 34)
        output_lines.append("")
        for ts_dir in timestamp_dirs:
            results_file = data_dir / ts_dir.name / "results.txt"

            output_lines.extend(analyze_timestamp_folder(ts_dir))

            results_file.write_text("\n".join(output_lines), encoding="utf-8")
            print(f"Wrote {results_file}")


if __name__ == "__main__":
    main()