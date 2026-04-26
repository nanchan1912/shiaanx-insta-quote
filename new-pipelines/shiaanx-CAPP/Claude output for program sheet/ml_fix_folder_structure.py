"""
ml_fix_folder_structure.py - One-time cleanup script.

Moves all flat *_features.json files from step/test/ into stem/ subfolders:
  test/2274_features.json  →  test/2274/2274_features.json

Also fixes ml_batch_extract.py so future extractions go to subfolders.
Run once after ml_batch_extract.py finishes (or is interrupted).

Usage:
    conda run -n occ python "Claude output for program sheet/ml_fix_folder_structure.py"
"""

import shutil
from pathlib import Path

BASE      = Path(__file__).parent
STEP_TEST = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/step/test"


def main():
    flat_jsons = list(STEP_TEST.glob("*_features.json"))
    print(f"Found {len(flat_jsons)} flat *_features.json files to move.")

    moved = 0
    skipped = 0
    for src in sorted(flat_jsons):
        stem = src.stem.replace("_features", "")   # e.g. "2274"
        dest_dir = STEP_TEST / stem
        dest = dest_dir / src.name

        if dest.exists():
            src.unlink()   # already in subfolder, remove flat duplicate
            skipped += 1
            continue

        dest_dir.mkdir(exist_ok=True)
        shutil.move(str(src), str(dest))
        moved += 1

    print(f"Done: {moved} moved, {skipped} duplicates removed.")
    print("\nNext steps:")
    print("  1. Run ml_batch_extract.py to extract remaining parts (resumes cleanly).")
    print("  2. Run ml_train_classifier_v3.py to retrain on full dataset.")


if __name__ == "__main__":
    main()
