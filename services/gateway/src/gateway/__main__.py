"""Gateway CLI entry point.

Phase 2 wires the ``backtest`` subcommand; ``stream`` arrives in Phase 3.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

from trade_contracts.risk import KillSwitchState

from .backtest import iter_unified_signals, run_backtest, write_jsonl
from .config import GatewaySettings, RiskConfig

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gateway",
        description="Risk & Routing Gateway (UnifiedTradeSignal -> OrderRequest).",
    )
    subparsers = p.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser(
        "backtest",
        help="UnifiedTradeSignal JSONL を検証し OrderRequest JSONL + 拒否ログを書き出す",
    )
    bt.add_argument(
        "--input",
        dest="input_path",
        type=Path,
        required=True,
        help="UnifiedTradeSignal JSONL のパス (aggregator backtest の出力)。",
    )
    bt.add_argument(
        "--state",
        dest="state_path",
        type=Path,
        required=True,
        help="初期 KillSwitchState を表す JSON ファイル。",
    )
    bt.add_argument(
        "--entry-price",
        dest="entry_price",
        type=Decimal,
        required=True,
        help="全シグナル共通のフラットなエントリー価格 (Phase 2 簡易モデル)。",
    )
    bt.add_argument(
        "--output-approved",
        dest="output_approved",
        type=Path,
        required=True,
        help="承認された OrderRequest JSONL の出力先。",
    )
    bt.add_argument(
        "--output-rejected",
        dest="output_rejected",
        type=Path,
        default=None,
        help="拒否ログ JSONL の出力先。省略時は書き出さない。",
    )
    bt.add_argument(
        "--positions",
        dest="positions_path",
        type=Path,
        default=None,
        help="初期ポジション JSON ({symbol: quantity})。SELL 検証用。",
    )
    bt.add_argument(
        "--capital",
        dest="capital",
        type=Decimal,
        default=None,
        help="資金額。省略時は CAPITAL 環境変数の値を使用。",
    )

    subparsers.add_parser(
        "stream",
        help="(Phase 3) trade-signals を購読し live-orders / paper-orders に publish する",
    )
    return p


def _load_state(path: Path) -> KillSwitchState:
    return KillSwitchState.model_validate_json(path.read_text(encoding="utf-8"))


def _load_positions(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"positions file must be a JSON object, got: {type(raw).__name__}")
    return {str(k): int(v) for k, v in raw.items()}


def _run_backtest_cmd(
    *,
    input_path: Path,
    state_path: Path,
    entry_price: Decimal,
    output_approved: Path,
    output_rejected: Path | None,
    positions_path: Path | None,
    capital: Decimal | None,
) -> int:
    settings = GatewaySettings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not input_path.exists():
        logger.error("input not found: %s", input_path)
        return 2
    if not state_path.exists():
        logger.error("state not found: %s", state_path)
        return 2
    if positions_path is not None and not positions_path.exists():
        logger.error("positions file not found: %s", positions_path)
        return 2

    state = _load_state(state_path)
    positions = _load_positions(positions_path)

    risk_config = RiskConfig.from_settings(settings)
    if capital is not None:
        risk_config = RiskConfig(
            capital=capital,
            max_risk_per_trade_pct=risk_config.max_risk_per_trade_pct,
            swing_risk_scale=risk_config.swing_risk_scale,
            default_stop_loss_spread_pct=risk_config.default_stop_loss_spread_pct,
            min_lot_size=risk_config.min_lot_size,
        )

    summary = run_backtest(
        iter_unified_signals(input_path),
        state=state,
        risk_config=risk_config,
        entry_price=entry_price,
        positions=positions,
    )

    write_jsonl(summary.approved, output_approved)
    if output_rejected is not None:
        write_jsonl(summary.rejected, output_rejected)

    logger.info(
        "backtest done: input=%s approved=%d (%s) rejected=%d (%s)",
        input_path,
        summary.approved_count,
        output_approved,
        summary.rejected_count,
        output_rejected if output_rejected is not None else "<suppressed>",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backtest":
        return _run_backtest_cmd(
            input_path=args.input_path,
            state_path=args.state_path,
            entry_price=args.entry_price,
            output_approved=args.output_approved,
            output_rejected=args.output_rejected,
            positions_path=args.positions_path,
            capital=args.capital,
        )
    if args.command == "stream":
        print("gateway stream is not implemented yet (Phase 3).", file=sys.stderr)
        return 2
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
