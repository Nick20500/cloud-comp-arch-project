from pathlib import Path
import csv
import re

NO_INTERFERENCE_ALIASES = {
    "no interference",
    "no-interference",
    "no_interference",
    "nointerference",
    "none",
    "baseline",
}


def parse_real_time_seconds(text: str) -> float | None:
    """
    Extracts the first 'real' runtime from log text.
    Supports formats like:
      real    2m15.291s
      real    15.291s
      real    15.291
    """
    match = re.search(r"^\s*real\s+((?:(\d+)m)?([\d.]+)s?)\s*$", text, re.MULTILINE)
    if not match:
        return None

    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = float(match.group(3))
    return minutes * 60 + seconds


def parse_benchmark_and_interference(file_stem: str) -> tuple[str, str]:
    """
    Best-effort parsing from filename stem.
    Tries common separators and patterns.
    """

    parts = file_stem.split("__")
    if len(parts) >= 2:
        benchmark, interference = parts[0], parts[1]
        return benchmark.strip(), interference.strip()

    return file_stem.strip(), "unknown"


def display_interference_name(label: str) -> str:
    normalized = label.lower().replace("-", " ").replace("_", " ").strip()
    if normalized in {a.replace("-", " ").replace("_", " ") for a in NO_INTERFERENCE_ALIASES}:
        return "none"

    return label.replace("ibench-", "").strip()

def is_no_interference(label: str) -> bool:
    normalized = label.lower().replace("-", " ").replace("_", " ").strip()
    return normalized in {a.replace("-", " ").replace("_", " ") for a in NO_INTERFERENCE_ALIASES}


def process_timestamp_folder(folder: Path) -> None:
    txt_files = sorted(
        f
        for f in folder.glob("*.txt")
        if f.is_file() and f.name.lower() not in {"result.csv", "result.txt", "results.txt"}
    )

    entries = []
    for file_path in txt_files:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        exec_seconds = parse_real_time_seconds(content)
        benchmark, interference = parse_benchmark_and_interference(file_path.stem)

        entries.append(
            {
                "file": file_path.name,
                "benchmark": benchmark,
                "interference": interference,
                "seconds": exec_seconds,
            }
        )

    # Find baseline (no interference) per benchmark
    baseline_by_benchmark: dict[str, float] = {}
    for e in entries:
        if e["seconds"] is not None and is_no_interference(e["interference"]):
            baseline_by_benchmark[e["benchmark"]] = e["seconds"]

    # Build matrix rows and columns.
    benchmarks = sorted({e["benchmark"] for e in entries})
    interference_order = ["none"]
    seen_interferences = set()
    for e in entries:
        name = display_interference_name(e["interference"])
        if name == "none":
            continue
        if name not in seen_interferences:
            seen_interferences.add(name)
            interference_order.append(name)

    values: dict[tuple[str, str], str] = {}
    for e in entries:
        benchmark = e["benchmark"]
        interference_name = display_interference_name(e["interference"])
        runtime = e["seconds"]
        baseline = baseline_by_benchmark.get(benchmark)

        if runtime is None:
            normalized = "N/A"
        elif interference_name == "none":
            normalized = "1.00"
        elif baseline is None or baseline == 0:
            normalized = "N/A"
        else:
            normalized = f"{round(runtime / baseline, 2):.2f}"

        values[(benchmark, interference_name)] = normalized

    # Write CSV matrix.
    out_file = folder / "result.csv"

    with out_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["workload", *interference_order])
        for benchmark in benchmarks:
            row = [benchmark]
            for interference_name in interference_order:
                row.append(values.get((benchmark, interference_name), "N/A"))
            writer.writerow(row)

    print(f"Wrote: {out_file}")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir / "data"

    if not data_dir.exists() or not data_dir.is_dir():
        raise FileNotFoundError(f"Data folder not found: {data_dir}")

    timestamp_folders = sorted(p for p in data_dir.iterdir() if p.is_dir())

    for folder in timestamp_folders:
        result_file = folder / "result.csv"
        if result_file.exists():
            print(f"Skipping {folder.name}: {result_file.name} already exists")
            continue
        process_timestamp_folder(folder)


if __name__ == "__main__":
    main()