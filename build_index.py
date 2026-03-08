import json
import os
from pathlib import Path

runs_dir = Path("runs")
index = []

for run_folder in sorted(runs_dir.iterdir()):
    if not run_folder.is_dir():
        continue

    report_path = run_folder / "report.json"
    html_path = run_folder / "report.html"

    if not report_path.exists():
        continue

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        if not report:
            continue

        final_step = report[-1]
        folder_name = run_folder.name
        parts = folder_name.split("_")

        # First two parts are date and time
        run_id = "_".join(parts[:2])
        test_name = "_".join(parts[2:]) if len(parts) > 2 else folder_name

        index.append({
            "run_id": run_id,
            "test_name": test_name,
            "steps": len(report),
            "final_status": final_step.get("pass_fail", "unknown"),
            "verdict": final_step.get("verdict", ""),
            "html_path": str(html_path).replace("\\", "/"),
            "json_path": str(report_path).replace("\\", "/")
        })

    except Exception as e:
        print(f"Skipping {run_folder.name}: {e}")

index.sort(key=lambda x: x["run_id"], reverse=True)

os.makedirs(runs_dir, exist_ok=True)
with open(runs_dir / "index.json", "w", encoding="utf-8") as f:
    json.dump(index, f, indent=2)

print(f"✅ Indexed {len(index)} reports → runs/index.json")