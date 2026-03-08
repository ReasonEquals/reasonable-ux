import json
import os
from pathlib import Path

runs_dir = Path("runs")
index = []
suite_index = []

for run_folder in sorted(runs_dir.iterdir()):
    if not run_folder.is_dir():
        continue

    folder_name = run_folder.name

    # --- Suite runs ---
    if folder_name.startswith("suite_"):
        suite_report_path = run_folder / "suite_report.html"
        suite_json_path = run_folder / "suite_report.json"
        if not suite_report_path.exists():
            continue

        # Count results by scanning child report.json files
        passed = failed = errors = timeouts = skipped = total = 0
        for child in run_folder.iterdir():
            if not child.is_dir():
                continue
            child_report = child / "report.json"
            if not child_report.exists():
                continue
            try:
                with open(child_report, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    total += 1
                    status = data[-1].get("pass_fail", "unknown")
                    if status == "pass": passed += 1
                    elif status == "fail": failed += 1
                    else: errors += 1
            except:
                continue

        parts = folder_name.split("_")
        suite_id = "_".join(parts[1:3]) if len(parts) >= 3 else folder_name

        suite_index.append({
            "suite_id": suite_id,
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "suite_status": "pass" if failed == 0 and errors == 0 else "fail",
            "html_path": str(suite_report_path).replace("\\", "/"),
        })
        continue

    # --- Individual runs ---
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
        parts = folder_name.split("_")
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
        print(f"Skipping {folder_name}: {e}")

index.sort(key=lambda x: x["run_id"], reverse=True)
suite_index.sort(key=lambda x: x["suite_id"], reverse=True)

os.makedirs(runs_dir, exist_ok=True)

with open(runs_dir / "index.json", "w", encoding="utf-8") as f:
    json.dump(index, f, indent=2)

with open(runs_dir / "suite_index.json", "w", encoding="utf-8") as f:
    json.dump(suite_index, f, indent=2)

print(f"✅ Indexed {len(index)} runs → runs/index.json")
print(f"✅ Indexed {len(suite_index)} suites → runs/suite_index.json")