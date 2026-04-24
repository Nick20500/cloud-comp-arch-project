import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Configuration
DATA_DIR = './data'
X_LIMIT = 80000
Y_LIMIT = 6.0  # ms (adjust if your latency is higher)

def parse_scan_file(filepath):
    """Extracts all QPS and p95 latency points from a multi-line mcperf scan file."""
    data_points = []
    scan_row = 0
    try:
        with open(filepath, 'r') as f:
            for line in f:
                # Look for lines starting with 'read'
                if line.startswith('read'):
                    parts = line.split()
                    # index 12 is p95, index 16 is QPS (based on your header)
                    p95 = float(parts[12]) / 1000.0 # Convert us to ms
                    qps = float(parts[16])
                    data_points.append({'scan_row': scan_row, 'qps': qps, 'p95': p95})
                    scan_row += 1
        return data_points
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return []

# 1. Gather all data points from all files
all_data = []

for filename in os.listdir(DATA_DIR):
    if filename.endswith(".txt"):
        # Format: timestamp_runi_interference-name.txt
        parts = filename.replace(".txt", "").split("_")
        if len(parts) < 3: continue
        
        interference = parts[-1]
        
        path = os.path.join(DATA_DIR, filename)
        points = parse_scan_file(path)
        
        for p in points:
            all_data.append({
                'filename': filename,
                'interference': interference,
                'scan_row': p['scan_row'],
                'qps': p['qps'],
                'p95': p['p95']
            })

df = pd.DataFrame(all_data)

# 2. Ensure we have parsed data
if df.empty:
    raise ValueError(f"No data points found in {DATA_DIR}. Check input files and parser format.")

# 3. Align by scan row index and aggregate across runs for each interference.
stats = df.groupby(['interference', 'scan_row']).agg(
    mean_p95=('p95', 'mean'),
    std_p95=('p95', 'std'),
    mean_qps=('qps', 'mean'),
    std_qps=('qps', 'std'),
    count=('p95', 'count')
).reset_index()

# 4. Plotting
plt.figure(figsize=(12, 7))

# Set a style or color cycle
colors = plt.get_cmap('tab10', 7)

for i, interference in enumerate(stats['interference'].unique()):
    subset = stats[stats['interference'] == interference].sort_values('scan_row')
    
    plt.errorbar(
        subset['mean_qps'], subset['mean_p95'],
        xerr=subset['std_qps'], yerr=subset['std_p95'],
        label=interference,
        fmt='-o', capsize=3, color=colors(i), markersize=4
    )

# 5. Formatting per ETH Requirements
plt.title('Memcached Throughput-Latency Profile under Interference', fontsize=14)
plt.xlabel('Achieved Throughput (QPS)', fontsize=12)
plt.ylabel('95th Percentile Latency (ms)', fontsize=12)
plt.xlim(0, X_LIMIT)
plt.ylim(0, Y_LIMIT)
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.legend(title="Interference Configuration", loc='upper left')

# Note about averaging
runs_count = df.groupby(['interference', 'scan_row']).size().max()
plt.annotate(f"Data averaged over {runs_count} runs per point\nError bars: ±1 Std Dev", 
             xy=(0.02, 0.02), xycoords='axes fraction', fontsize=10, 
             bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8))

plt.tight_layout()
plt.savefig('plot_part1.png', dpi=300)
print(f"Success! Plot saved as 'plot_part1.png'. Processed {len(df)} total data points.")