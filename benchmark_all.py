
import subprocess
import time
import shutil
from pathlib import Path

# 配置
UID = "677013800"
COUNT = 5
SUMMARY_DIR = Path(f"summary/users/{UID}")

SCENARIOS = [
    {"name": "Serial (10 tasks) - Flash", "concurrency": 1, "model": "GLM-4.7-Flash"},
    {"name": "Parallel (10 tasks) - Flash", "concurrency": 10, "model": "GLM-4.7-Flash"},
    {"name": "Serial (10 tasks) - FlashX", "concurrency": 1, "model": "GLM-4-FlashX-250414"},
    {"name": "Parallel (10 tasks) - FlashX", "concurrency": 10, "model": "GLM-4-FlashX-250414"},
]

def clear_cache():
    if SUMMARY_DIR.exists():
        shutil.rmtree(SUMMARY_DIR)
        print("🧹 Cache cleared.")

def run_scenario(scenario):
    print(f"\n🚀 Running: {scenario['name']}")
    clear_cache()
    
    cmd = [
        "python", "summarize.py",
        "--user", UID,
        "--count", str(COUNT),
        "--concurrency", str(scenario['concurrency']),
        "--model", scenario['model']
    ]
    
    start_time = time.time()
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e}")
        return 0
    end_time = time.time()
    
    duration = end_time - start_time
    print(f"⏱️  Duration: {duration:.2f}s")
    return duration

def main():
    results = []
    print(f"🏎️  Starting Benchmark: {COUNT} videos")
    
    for scenario in SCENARIOS:
        duration = run_scenario(scenario)
        results.append({**scenario, "duration": duration})
    
    print("\n\n📊 Benchmark Results:")
    print(f"{'-'*80}")
    print(f"{'Scenario':<30} | {'Concurrency':<12} | {'Model':<20} | {'Time (s)':<10}")
    print(f"{'-'*80}")
    
    for r in results:
        print(f"{r['name']:<30} | {r['concurrency']:<12} | {r['model']:<20} | {r['duration']:.2f}")
    print(f"{'-'*80}")

if __name__ == "__main__":
    main()
