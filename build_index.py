import json
import os
from pathlib import Path

reports_dir = Path("reports")
index = []

for file in sorted(reports_dir.glob("*.json")):
    if file.name == "index.json":
        continue

    try:
        with open(file, "r", encoding="utf-8") as f:
            report = json.load(f)

        if not report:
            continue

        final_step = report[-1]
        parts = file.stem.split("_")
        timestamp_idx = next((i for i, p in enumerate(parts) if len(p) == 8 and p.isdigit()), None)

        if timestamp_idx is not None:
            test_name = "_".join(parts[:timestamp_idx])
            run_id = "_".join(parts[timestamp_idx:])
        else:
            test_name = file.stem
            run_id = file.stem

        html_path = f"reports/{file.stem}.html"

        index.append({
            "run_id": run_id,
            "test_name": test_name,
            "steps": len(report),
            "final_status": final_step.get("pass_fail", "unknown"),
            "verdict": final_step.get("verdict", ""),
            "html_path": html_path,
            "json_path": str(file)
        })

    except Exception as e:
        print(f"Skipping {file.name}: {e}")

index.sort(key=lambda x: x["run_id"], reverse=True)

with open(reports_dir / "index.json", "w", encoding="utf-8") as f:
    json.dump(index, f, indent=2)

print(f"✅ Indexed {len(index)} reports → reports/index.json")