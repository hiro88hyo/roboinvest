"""OMS Paper CLI entry point.

Phase 2 では ``backtest`` サブコマンドを提供する。``stream`` は Phase 3 で
実装するため、現状はスタブで終了する。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from trade_contracts.enums import TradingStyle

from .backtest import (
    iter_order_books,
    iter_order_requests,
    read_positions_json,
    run_backtest,
    write_jsonl,
    write_positions_json,
)
from .config import OmsPaperSettings

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oms-paper",
        description="OMS Paper: paper-orders + raw-market-data -> simulated fills.",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="OrderRequest JSONL と OrderBookSnapshot JSONL から擬似約定を再生する",
    )
    bt.add_argument(
        "--orders",
        dest="orders_path",
        type=Path,
        required=True,
        help="OrderRequest JSONL のパス (gateway backtest の --output-approved)。",
    )
    bt.add_argument(
        "--books",
        dest="books_path",
        type=Path,
        required=True,
        help="OrderBookSnapshot JSONL のパス。",
    )
    bt.add_argument(
        "--positions",
        dest="positions_path",
        type=Path,
        default=None,
        help="初期 positions JSON ({symbol: PaperPosition})。省略時は空。",
    )
    bt.add_argument(
        "--output-fills",
        dest="output_fills",
        type=Path,
        required=True,
        help="擬似約定 JSONL (PaperFillRecord) の出力先。",
    )
    bt.add_argument(
        "--output-positions",
        dest="output_positions",
        type=Path,
        required=True,
        help="終了時 positions JSON の出力先。",
    )
    bt.add_argument(
        "--output-rejected",
        dest="output_rejected",
        type=Path,
        default=None,
        help="不約定ログ JSONL (NoFillRecord) の出力先。省略時は書き出さない。",
    )
    bt.add_argument(
        "--default-holding-type",
        dest="default_holding_type",
        type=TradingStyle,
        choices=list(TradingStyle),
        default=None,
        help="OrderRequest が holding_type を持たないため、新規 BUY 時に使う値。"
        "未指定時は環境変数 DEFAULT_HOLDING_TYPE (デフォルト day)。",
    )

    subparsers.add_parser(
        "stream",
        help="paper-orders と raw-market-data を購読する常駐ループ (Phase 3 未実装)",
    )
    return p


def _run_backtest_cmd(
    *,
    orders_path: Path,
    books_path: Path,
    positions_path: Path | None,
    output_fills: Path,
    output_positions: Path,
    output_rejected: Path | None,
    default_holding_type: TradingStyle | None,
) -> int:
    settings = OmsPaperSettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not orders_path.exists():
        logger.error("orders not found: %s", orders_path)
        return 2
    if not books_path.exists():
        logger.error("books not found: %s", books_path)
        return 2
    if positions_path is not None and not positions_path.exists():
        logger.error("positions file not found: %s", positions_path)
        return 2

    initial_positions = read_positions_json(positions_path) if positions_path else {}
    holding_type = default_holding_type or settings.default_holding_type

    summary = run_backtest(
        orders=iter_order_requests(orders_path),
        books=iter_order_books(books_path),
        initial_positions=initial_positions,
        default_holding_type=holding_type,
    )

    write_jsonl(summary.fills, output_fills)
    write_positions_json(summary.final_positions, output_positions)
    if output_rejected is not None:
        write_jsonl(summary.no_fills, output_rejected)

    logger.info(
        "backtest done: orders=%d fills=%d (%s) no_fills=%d (%s) positions=%s",
        summary.order_count,
        summary.fill_count,
        output_fills,
        summary.no_fill_count,
        output_rejected if output_rejected is not None else "<suppressed>",
        output_positions,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backtest":
        return _run_backtest_cmd(
            orders_path=args.orders_path,
            books_path=args.books_path,
            positions_path=args.positions_path,
            output_fills=args.output_fills,
            output_positions=args.output_positions,
            output_rejected=args.output_rejected,
            default_holding_type=args.default_holding_type,
        )
    if args.command == "stream":
        print(
            "oms-paper stream is not implemented yet (Phase 3).",
            file=sys.stderr,
        )
        return 2
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
