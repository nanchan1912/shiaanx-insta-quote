"""Temporary script — explore .h5 file structure."""
import h5py
import numpy as np
from pathlib import Path

H5_DIR = Path(__file__).parent / "Dataset/MFCAD_dataset/MFCAD++_dataset/hierarchical_graphs"

for split in ["training", "val", "test"]:
    path = H5_DIR / f"{split}_MFCAD++.h5"
    print(f"\n{'='*60}")
    print(f"{split.upper()}  —  {path.name}")
    print(f"{'='*60}")
    with h5py.File(path, "r") as f:
        print(f"Top-level keys: {list(f.keys())}")
        for grp_name in list(f.keys())[:2]:  # first 2 batches
            grp = f[grp_name]
            print(f"\n  Group: {grp_name}")
            print(f"  Keys: {list(grp.keys())}")
            for key in grp.keys():
                ds = grp[key]
                print(f"    {key}: shape={ds.shape}  dtype={ds.dtype}", end="")
                if ds.shape[0] <= 5:
                    print(f"  values={ds[:]}")
                else:
                    print(f"  first_row={ds[0]}")
