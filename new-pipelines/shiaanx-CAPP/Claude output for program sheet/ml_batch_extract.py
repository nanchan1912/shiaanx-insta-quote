"""
ml_batch_extract.py - Generate *_features.json for all MFCAD++ test STEP files.

Runs extract_features.py on every STEP file in the test set that does not
already have a corresponding *_features.json.  Output files are placed next
to each STEP file (or in a stem/ subdirectory if the STEP is in one).

This only needs to run once.  Subsequent runs skip already-extracted parts.

Usage:
    conda run -n occ python "Claude output for program sheet/ml_batch_extract.py"
    conda run -n occ python "Claude output for program sheet/ml_batch_extract.py" --limit 500
    conda run -n occ python "Claude output for program sheet/ml_batch_extract.py" --workers 4
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

BASE      = Path(__file__).parent
STEP_TEST = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/step/test"


def features_path_for(step_path: Path) -> Path:
    """Always put *_features.json inside a stem/ subdirectory."""
    stem = step_path.stem
    return step_path.parent / stem / f"{stem}_features.json"



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N STEP files (for quick testing)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers (experimental; 1 = serial)")
    args = parser.parse_args()

    extract_script = str(BASE / "1. extract_features.py")
    python_exe = sys.executable

    all_steps = sorted(STEP_TEST.rglob("*.step"))
    if args.limit:
        all_steps = all_steps[: args.limit]

    todo = [p for p in all_steps if not features_path_for(p).exists()]
    already_done = len(all_steps) - len(todo)

    print(f"MFCAD++ test batch extractor")
    print(f"  Total STEP files : {len(all_steps)}")
    print(f"  Already extracted: {already_done}")
    print(f"  To process       : {len(todo)}")

    if not todo:
        print("  Nothing to do.")
        return

    t0 = time.time()
    ok = 0
    fail = 0

    for i, step_path in enumerate(todo):
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(todo) - i - 1) / rate if rate > 0 else 0
        print(f"  {i+1}/{len(todo)} | {step_path.name:30s} | "
              f"{elapsed:.0f}s elapsed | ETA {eta:.0f}s", end="\r")

        out_path = str(features_path_for(step_path))
        try:
            result = subprocess.run(
                [python_exe, extract_script, str(step_path), out_path],
                capture_output=True, timeout=60,
            )
            if result.returncode == 0 and Path(out_path).exists():
                ok += 1
            else:
                fail += 1
                if fail <= 5:  # only print first few errors
                    print(f"\n  [FAIL] {step_path.name}: {result.stderr.decode()[:200]}")
        except subprocess.TimeoutExpired:
            fail += 1
            if fail <= 5:
                print(f"\n  [TIMEOUT] {step_path.name}")
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"\n  [ERROR] {step_path.name}: {e}")

    total = time.time() - t0
    print(f"\nDone: {ok} extracted, {fail} failed, {total:.1f}s total "
          f"({total/max(ok,1):.2f}s/part)")


if __name__ == "__main__":
    main()
