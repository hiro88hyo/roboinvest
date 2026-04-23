"""Gateway CLI entry point.

Phase 1 only wires up a ``--help``-able stub; ``backtest`` / ``stream``
subcommands arrive in Phase 2 and Phase 3 respectively.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="Risk & Routing Gateway (UnifiedTradeSignal -> OrderRequest).",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["backtest", "stream"],
        help="Run mode (Phase 2 / Phase 3; not yet implemented).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode is None:
        parser.print_help()
        return 0
    print(
        f"gateway mode '{args.mode}' is not implemented yet (Phase 1 ships risk logic only).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
