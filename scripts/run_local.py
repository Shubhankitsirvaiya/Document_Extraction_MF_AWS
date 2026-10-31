from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.agent.graph import run_pipeline
from app.common.config import ROOT_DIR, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one prospectus extraction locally in mock mode"
    )
    parser.add_argument("pdf", type=Path, help="Path to a PDF with 1–20 pages")
    parser.add_argument("--all-pages", action="store_true", help="Bypass regex page filtering")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "generated" / "cli")
    args = parser.parse_args()

    settings = load_settings(overrides={"APP_MODE": "local_mock"})
    job_id = str(uuid4())
    attempt_id = str(uuid4())
    output_dir = args.output / job_id

    def progress(stage: str, percentage: int) -> None:
        print(f"[{percentage:3d}%] {stage}")

    state = run_pipeline(
        args.pdf.resolve(),
        output_dir,
        settings,
        job_id=job_id,
        attempt_id=attempt_id,
        process_all_pages=args.all_pages,
        progress=progress,
    )
    print(
        json.dumps(
            {key: state.get(key) for key in ("status", "result_path", "warnings", "errors")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
