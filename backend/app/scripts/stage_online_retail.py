"""Validate or explicitly stage Online Retail II source rows."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.staging.online_retail import (
    SqlAlchemyStagingRepository,
    create_staging_tables,
    render_validation_report,
    stage_workbook,
)


def main() -> int:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        type=Path,
        default=project_root / "data" / "raw" / "online_retail_II.xlsx",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=project_root / "data" / "processed" / "online_retail_staging_validation.md",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create/update isolated staging tables and insert source rows.",
    )
    args = parser.parse_args()
    repository = None
    if args.write:
        from app.db.session import engine

        create_staging_tables(engine)
        repository = SqlAlchemyStagingRepository(engine)
    summary, load_id = stage_workbook(
        args.file,
        repository=repository,
        write=args.write,
        report_path=args.report,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_validation_report(summary, load_id=load_id, write=args.write),
        encoding="utf-8",
    )
    print(f"Validation report: {args.report}")
    print(f"Rows read: {summary.rows_read}")
    print(f"Rows staged: {summary.rows_staged}")
    print(f"Mode: {'staging write' if args.write else 'validation-only dry run'}")
    if load_id is not None:
        print(f"Load ID: {load_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
