from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=max(2, span // 2)).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    h, l, c = df["high"], df["low"], df["close"]
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    a = atr(df, period)
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return adx_val, plus_di, minus_di


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, 12) - ema(close, 26)
    signal = ema(line, 9)
    hist = line - signal
    return line, signal, hist


def awesome_oscillator(df: pd.DataFrame) -> pd.Series:
    mid = (df["high"] + df["low"]) / 2.0
    return mid.rolling(5).mean() - mid.rolling(34).mean()


def stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    low = df["low"].rolling(period).min()
    high = df["high"].rolling(period).max()
    k = 100 * (df["close"] - low) / (high - low).replace(0, np.nan)
    d = k.rolling(smooth).mean()
    return k, d


def pct_rank(values: pd.Series, ascending: bool = True) -> pd.Series:
    return values.rank(pct=True, ascending=ascending, method="average")
