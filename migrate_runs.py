import os
import json
import shutil
from pathlib import Path

reports_dir = Path("reports")
screenshots_dir = Path("screenshots")
runs_dir = Path("runs")
runs_dir.mkdir(exist_ok=True)

# Find all JSON reports (excluding index.json)
report_files = [f for f in reports_dir.glob("*.json") if f.name != "index.json"]

migrated = 0
skipped = 0

for report_file in sorted(report_files):
    try:
        # Parse run_id and test_name from filename
        stem = report_file.stem
        parts = stem.split("_")
        timestamp_idx = next((i for i, p in enumerate(parts) if len(p) == 8 and p.isdigit()), None)

        if timestamp_idx is None:
            print(f"⚠️  Skipping {report_file.name} — can't parse timestamp")
            skipped += 1
            continue

        date = parts[timestamp_idx]
        time = parts[timestamp_idx + 1] if timestamp_idx + 1 < len(parts) else "000000"
        test_name = "_".join(parts[:timestamp_idx]) if timestamp_idx > 0 else "unknown"
        run_id = f"{date}_{time}"
        folder_name = f"{run_id}_{test_name}"

        run_dir = runs_dir / folder_name
        run_screenshots_dir = run_dir / "screenshots"
        run_dir.mkdir(exist_ok=True)
        run_screenshots_dir.mkdir(exist_ok=True)

        # Copy report.json
        shutil.copy(report_file, run_dir / "report.json")

        # Copy matching HTML report if it exists
        html_file = reports_dir / f"{stem}.html"
        if html_file.exists():
            shutil.copy(html_file, run_dir / "report.html")

        # Copy matching screenshots
        prefix = f"{stem}_step_"
        for screenshot in screenshots_dir.glob(f"*{run_id}*"):
            # Extract step number and rename to step_N.png
            name = screenshot.stem
            step_part = name.split("_step_")[-1] if "_step_" in name else None
            if step_part:
                dest = run_screenshots_dir / f"step_{step_part}.png"
                shutil.copy(screenshot, dest)

        print(f"✅ Migrated → {folder_name}")
        migrated += 1

    except Exception as e:
        print(f"❌ Error migrating {report_file.name}: {e}")
        skipped += 1

print(f"\nDone — {migrated} migrated, {skipped} skipped")
print("Original files left in place. Delete reports/ and screenshots/ manually once verified.")