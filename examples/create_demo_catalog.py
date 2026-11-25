"""Compatibility wrapper for creating the synthetic sales example."""

from __future__ import annotations

import argparse
from pathlib import Path

from d2d.demo import create_demo_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/analytics.duckdb"))
    args = parser.parse_args()
    destination = create_demo_catalog(args.output)
    print(f"Created synthetic read-only demo catalog: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
