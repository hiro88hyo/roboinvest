"""Pure-function technical indicators operating on Polars DataFrames.

各関数は以下を満たす:

- 副作用なし・入力 DataFrame を破壊しない
- `symbol` 列が存在すれば銘柄ごとに独立して計算する
- ウォームアップ期間は null を返し、例外で落とさない
- 内部計算は Float64。Decimal への復元は上位レイヤ (`ProcessedFeatures` 生成時) で行う
- 入力は時系列昇順にソート済みであることを前提とする
"""

from .bollinger import bollinger
from .moving_average import sma
from .rsi import rsi
from .volume_ratio import volume_ratio
from .vwap import vwap

__all__ = ["bollinger", "rsi", "sma", "volume_ratio", "vwap"]
