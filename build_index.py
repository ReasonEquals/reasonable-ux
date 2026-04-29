import json
import os
from pathlib import Path


def _load_cost_summary(folder: Path):
    cost_path = folder / "cost_summary.json"
    if not cost_path.exists():
        return None
    try:
        with open(cost_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _index_individual_run(run_folder: Path, runs_dir: Path, index: list) -> None:
    """Append one entry to `index` for a folder containing report.json directly."""
    report_path = run_folder / "report.json"
    html_path = run_folder / "report.html"

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        if not report:
            return
        final_step = report[-1]

        cost_data = _load_cost_summary(run_folder) or {}
        total_tokens = cost_data.get("total_tokens")

        # Run id / test name derivation:
        # - Nested layout (current): runs/<domain>/<YYYY-MM-DD_HHMMSS>_single_page/
        #     run_id = "<domain>/<timestamp>", test_name = "<run_type>" (e.g. "single_page").
        # - Flat layout (legacy/defensive): runs/<YYYYMMDD_HHMM>_<test_name>/
        #     run_id = "YYYYMMDD_HHMM", test_name = "<rest>".
        rel = run_folder.relative_to(runs_dir)
        if len(rel.parts) >= 2:
            domain = rel.parts[0]
            stem = rel.parts[-1]
            parts = stem.split("_")
            ts_parts = parts[:2] if len(parts) >= 2 else parts
            run_id = f"{domain}/{'_'.join(ts_parts)}"
            test_name = "_".join(parts[2:]) if len(parts) > 2 else stem
        else:
            parts = run_folder.name.split("_")
            run_id = "_".join(parts[:2])
            test_name = "_".join(parts[2:]) if len(parts) > 2 else run_folder.name

        index.append({
            "run_id": run_id,
            "test_name": test_name,
            "steps": len(report),
            "final_status": final_step.get("pass_fail", "unknown"),
            "verdict": final_step.get("verdict", ""),
            "html_path": str(html_path).replace("\\", "/"),
            "json_path": str(report_path).replace("\\", "/"),
            "total_tokens": total_tokens,
        })
    except Exception as e:
        print(f"Skipping {run_folder}: {e}")


def main(runs_dir: Path = Path("runs")) -> None:
    runs_dir = Path(runs_dir)
    index: list = []
    suite_index: list = []

    if not runs_dir.exists():
        os.makedirs(runs_dir, exist_ok=True)

    for run_folder in sorted(runs_dir.iterdir()):
        if not run_folder.is_dir():
            continue

        folder_name = run_folder.name

        # --- Suite runs ---
        if folder_name.startswith("suite_"):
            # Sentinel: cost_summary.json or any *_multi_*.pdf (suite_report.html does
            # not exist for current suite runs; PDFs are the public artifact).
            cost_data = _load_cost_summary(run_folder)
            pdfs = sorted(run_folder.glob("*_multi_*.pdf"))
            if not cost_data and not pdfs:
                continue

            passed = failed = errors = total = 0
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
                        if status == "pass":
                            passed += 1
                        elif status == "fail":
                            failed += 1
                        else:
                            errors += 1
                except (OSError, json.JSONDecodeError, KeyError, IndexError):
                    continue

            total_tokens = (cost_data or {}).get("total_tokens")
            html_path = pdfs[0] if pdfs else (run_folder / "")

            parts = folder_name.split("_")
            suite_id = "_".join(parts[1:3]) if len(parts) >= 3 else folder_name

            suite_index.append({
                "suite_id": suite_id,
                "total": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "suite_status": "pass" if failed == 0 and errors == 0 else "fail",
                "html_path": str(html_path).replace("\\", "/"),
                "total_tokens": total_tokens,
            })
            continue

        # --- Individual runs (dual-path) ---
        # Flat layout: runs/<run>/report.json (legacy, defensive — no current instances).
        if (run_folder / "report.json").exists():
            _index_individual_run(run_folder, runs_dir, index)
            continue

        # Nested layout: runs/<domain>/<ts>_single_page/report.json (current).
        for child in sorted(run_folder.iterdir()):
            if child.is_dir() and (child / "report.json").exists():
                _index_individual_run(child, runs_dir, index)

    index.sort(key=lambda x: x["run_id"], reverse=True)
    suite_index.sort(key=lambda x: x["suite_id"], reverse=True)

    with open(runs_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    with open(runs_dir / "suite_index.json", "w", encoding="utf-8") as f:
        json.dump(suite_index, f, indent=2)

    print(f"✅ Indexed {len(index)} runs → {runs_dir / 'index.json'}")
    print(f"✅ Indexed {len(suite_index)} suites → {runs_dir / 'suite_index.json'}")

    # --- Variants index (batch 68 matrix surfaced for the dashboard) ---
    variants: list = []
    try:
        from compare_variants import build_rows

        rows = build_rows(runs_dir=runs_dir)
        variants = [
            {
                "variant": r.variant,
                "site": r.site,
                "suite_id": r.suite_id,
                "step_count": r.step_count,
                "total_tokens": r.total_tokens,
                "cost_usd": r.cost_usd,
                "tokens_per_step": r.tokens_per_step,
                "cta_clarity": r.cta_clarity,
                "copy_quality": r.copy_quality,
                "flow_smoothness": r.flow_smoothness,
                "composite_score": r.composite_score,
                "persona": r.persona,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"⚠️  variants index skipped: {e}")

    with open(runs_dir / "variants_index.json", "w", encoding="utf-8") as f:
        json.dump(variants, f, indent=2)
    print(f"✅ Indexed {len(variants)} variant rows → {runs_dir / 'variants_index.json'}")


if __name__ == "__main__":
    main()
