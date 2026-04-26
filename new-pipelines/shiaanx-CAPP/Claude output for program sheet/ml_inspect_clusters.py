"""Temporary script — inspect cluster fields for parts 21 and 25."""
import json, re
from pathlib import Path

BASE = Path(__file__).parent
DATASET = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset"

TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"
with open(TAXONOMY_PATH) as f:
    tax = json.load(f)
ID_TO_NAME = {m["mfcad_id"]: m["mfcad_name"] for m in tax["mappings"]}

FACE_RE = re.compile(r"ADVANCED_FACE\('(\d+)'", re.IGNORECASE)

def gt_labels(step_path):
    labels = []
    with open(step_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = FACE_RE.search(line)
            if m:
                labels.append(int(m.group(1)))
    return labels

for part in ["25", "21"]:
    json_path = DATASET / f"step/test/{part}/{part}_features_clustered_classified.json"
    step_path = DATASET / f"step/test/{part}/{part}.step"

    with open(json_path) as f:
        d = json.load(f)

    labels = gt_labels(step_path)

    print(f"\n{'='*70}")
    print(f"PART {part}  —  {len(d['clusters'])} clusters, {len(labels)} GT face labels")
    print(f"{'='*70}")

    for c in d["clusters"]:
        # majority-vote GT label for this cluster
        face_ids = c.get("face_indices", [])
        cluster_labels = [labels[i] for i in face_ids if i < len(labels)]
        if cluster_labels:
            from collections import Counter
            gt_id = Counter(cluster_labels).most_common(1)[0][0]
            gt_name = ID_TO_NAME.get(gt_id, f"unknown_{gt_id}")
        else:
            gt_id, gt_name = "?", "?"

        predicted = c.get("feature_type", "?")
        match = "✓" if (predicted.replace("_angled","") == gt_name.lower().replace(" ","_")
                        or predicted == gt_name.lower().replace(" ","_")
                        or predicted == gt_name.lower().replace("-","_").replace(" ","_")) else "✗"

        print(f"\n  Cluster {c.get('cluster_id'):>2}  GT={gt_name:<30} pred={predicted}")
        print(f"           seed={c.get('seed_type','?'):<12} "
              f"radii={c.get('radii')}  depth={c.get('depth')}  "
              f"face_count={c.get('face_count')}  "
              f"area={c.get('face_area')}  "
              f"perp_walls={c.get('has_perpendicular_walls')}({c.get('perp_wall_count',0)})")
