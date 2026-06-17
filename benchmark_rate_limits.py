
import subprocess
import time
import shutil
from pathlib import Path
import re

# 配置
COUNT = 10
CONCURRENCY = 10
SUMMARY_DIR = Path("summary/favorites")

SCENARIOS = [
    {"name": "GLM-4.7-Flash (Default)", "model": "GLM-4.7-Flash"},
    {"name": "GLM-4-FlashX-250414", "model": "GLM-4-FlashX-250414"},
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
        "--favorite",
        "--count", str(COUNT),
        "--concurrency", str(CONCURRENCY),
        "--model", scenario['model']
    ]
    
    start_time = time.time()
    try:
        # Capture text output to count 429s
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output = result.stdout + result.stderr
        print(output) # Print to console so we can see progress/errors
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e}")
        output = e.stdout + e.stderr
        print(output)
        return 0, 0
        
    end_time = time.time()
    duration = end_time - start_time
    
    # Count 429 occurrences
    retry_count = output.count("API 速率限制 (429)")
    
    print(f"⏱️  Duration: {duration:.2f}s")
    print(f"⚠️  Rate Limit Retries: {retry_count}")
    
    return duration, retry_count

def main():
    results = []
    print(f"🏎️  Starting Rate Limit Benchmark: {COUNT} videos, Concurrency {CONCURRENCY}")
    
    for scenario in SCENARIOS:
        duration, retries = run_scenario(scenario)
        results.append({**scenario, "duration": duration, "retries": retries})
    
    print("\n\n📊 Benchmark Results (Concurrency 10):")
    print(f"{'-'*80}")
    print(f"{'Model':<30} | {'Time (s)':<10} | {'429 Retries':<12}")
    print(f"{'-'*80}")
    
    for r in results:
        print(f"{r['name']:<30} | {r['duration']:.2f} | {r['retries']:<12}")
    print(f"{'-'*80}")

if __name__ == "__main__":
    main()
