"""
行情拉取模块
- A股: akshare.stock_zh_a_hist
- 黄金 ETF: akshare.fund_etf_hist_em  (带重试)
- 上金所基准金价: akshare.spot_golden_benchmark_sge
- 沪深300 基准: akshare.stock_zh_index_daily
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

import pandas as pd

from config_loader import config
from database import conn, init_db

logger = logging.getLogger("stock.data_fetcher")

KEEP_DAYS = 90
RETRY = 3


def _retry(fn, desc: str):
    for i in range(RETRY):
        try:
            r = fn()
            if r is not None:
                return r
        except Exception as e:
            logger.warning(f"{desc} attempt {i+1} failed: {e}")
        time.sleep(2 + i)
    return None


def fetch_stock_kline(code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """拉A股后复权日线 (优先新浪 stock_zh_a_daily, 备 em)"""
    import akshare as ak
    end_d = datetime.now().strftime("%Y%m%d")
    start_d = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    market = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{market}{code}"

    def _do():
            df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_d.replace("-", ""),
                                     end_date=end_d.replace("-", ""), adjust="qfq")
            if df is None or df.empty:
                return None
            return df[["date", "open", "high", "low", "close", "volume", "amount", "turnover"]]

    result = _retry(_do, f"stock {code} (sina)")
    if result is not None:
        result["date"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
        return result[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    # fallback: 东方财富
    def _em():
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_d, end_date=end_d, adjust="qfq")
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    return _retry(_em, f"stock {code} (em)")


def fetch_stock_market_cap(code: str) -> Optional[Dict]:
    """拉市值(单次)"""
    import akshare as ak
    try:
        df = ak.stock_individual_info_em(symbol=code)
        if df is None or df.empty:
            return None
        info = dict(zip(df["item"], df["value"]))

        def _to_float(v):
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return None

        return {
            "market_cap": _to_float(info.get("总市值")),
            "circulating_market_cap": _to_float(info.get("流通市值")),
            "pe": _to_float(info.get("市盈率(动)")),
        }
    except Exception as e:
        logger.warning(f"fetch market_cap {code}: {e}")
        return None


def fetch_gold_etf_kline(code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """拉黄金ETF日线 (518880 等). 优先 em, 备 sina。

    ⚠️ 绝不能 fallback 到上金所基准金价:
    SGE 是「元/克」的基准金价(800+), ETF 是「元/份」的基金份额(8.x)。
    混写会把 ETF 价位污染成金价(历史故障: 518880 有 56 天 close 是 800+)。
    ETF 拿不到就返回 None, 宁可空缺也不污染。
    """
    import akshare as ak
    end_d = datetime.now().strftime("%Y%m%d")
    start_d = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    start_d_iso = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _em():
        df = ak.fund_etf_hist_em(
            symbol=code, period="daily",
            start_date=start_d, end_date=end_d, adjust="qfq",
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["turnover"] = None
        return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    def _sina():
        # 新浪 ETF 日线: em 挂掉时的可靠备源(2026-11 实测可用)
        market = "sh" if code.startswith(("5", "6", "9")) else "sz"
        df = ak.fund_etf_hist_sina(symbol=f"{market}{code}")
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "volume": "volume", "amount": "amount",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        # 注意: start_d 是 %Y%m%d, 而 date 已是 %Y-%m-%d, 必须转成同一格式再比
        df = df[df["date"] >= start_d_iso]
        df["turnover"] = None
        return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    r = _retry(_em, f"gold ETF {code} (em)")
    if r is not None:
        return _guard_etf_scale(r, code)
    r = _retry(_sina, f"gold ETF {code} (sina)")
    if r is not None:
        return _guard_etf_scale(r, code)
    logger.warning(f"gold ETF {code} 无可用数据源, 返回 None (不再 fallback SGE)")
    return None


def _guard_etf_scale(df: pd.DataFrame, code: str) -> Optional[pd.DataFrame]:
    """ETF 单价合理性护栏: 黄金ETF 正常在 3~30 元/份, 超过 100 一定是串源。"""
    if df is None or df.empty:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    bad = close[close > 100]
    if len(bad) > 0:
        logger.error(
            f"gold ETF {code} 价格量纲异常 (close>100: {len(bad)} 行, "
            f"max={close.max()}), 疑似串到 SGE 金价, 丢弃整批"
        )
        return None
    return df


def fetch_sge_gold_kline(days: int = 120) -> Optional[pd.DataFrame]:
    """上金所 Au99.99 基准金价 (元/克)。

    优先 spot_hist_sge: 含完整 OHLC 且更新到最新交易日。
    spot_golden_benchmark_sge 只有早/晚盘价且常滞后数个交易日(2026-11 实测停在 11-05),
    作为备源。
    """
    import akshare as ak

    def _do():
        df = ak.spot_hist_sge(symbol="Au99.99")
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["volume"] = None
        df["amount"] = None
        df["turnover"] = None
        df = df.tail(days)
        return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    r = _retry(_do, "sge gold (spot_hist_sge)")
    if r is not None:
        return r

    def _do_bench():
        df = ak.spot_golden_benchmark_sge()
        if df is None or df.empty:
            return None
        df = df.rename(columns={"交易时间": "date", "早盘价": "early", "晚盘价": "late"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["close"] = (df["early"] + df["late"]) / 2
        df["open"] = df["close"]
        df["high"] = df[["early", "late"]].max(axis=1)
        df["low"] = df[["early", "late"]].min(axis=1)
        df["volume"] = None
        df["amount"] = None
        df["turnover"] = None
        df = df.tail(days)
        return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]

    logger.warning("sge gold fallback to spot_golden_benchmark_sge (数据可能滞后)")
    return _retry(_do_bench, "sge gold (benchmark)")


def fetch_benchmark_index(code: str = "000300", days: int = 120) -> Optional[pd.DataFrame]:
    """沪深300"""
    import akshare as ak

    def _do():
        df = ak.stock_zh_index_daily(symbol=f"sh{code}")
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df.tail(days)[["date", "close"]]

    return _retry(_do, f"index {code}")


def save_kline(code: str, df: pd.DataFrame):
    """保存K线到DB，自动清理 >90 天"""
    if df is None or df.empty:
        return 0
    rows = [(
        code, r["date"],
        r.get("open"), r.get("close"),
        r.get("high"), r.get("low"),
        r.get("volume"), r.get("amount"),
        r.get("turnover"),
    ) for _, r in df.iterrows()]
    with conn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO kline
               (code,date,open,close,high,low,volume,amount,turnover)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        c.execute("DELETE FROM kline WHERE date < ?", (cutoff,))
    return len(rows)


def update_today_market_cap(code: str, market_cap: Optional[float]):
    if market_cap is None:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        c.execute("UPDATE kline SET market_cap=? WHERE code=? AND date=?",
                  (market_cap, code, today))


def refresh_all() -> Dict:
    """刷新所有标的"""
    init_db()
    portfolio = config.get("portfolio", {})
    results = {"stocks": [], "gold": [], "benchmark": 0, "errors": []}

    for s in (portfolio.get("stocks") or []):
        if not s.get("enabled", True):
            continue
        code = s["code"]
        df = fetch_stock_kline(code)
        if df is not None:
            n = save_kline(code, df)
            mc = fetch_stock_market_cap(code)
            update_today_market_cap(code, mc.get("market_cap") if mc else None)
            results["stocks"].append({"code": code, "name": s.get("name"), "rows": n})
        else:
            results["errors"].append(f"stock {code} failed")

    for g in (portfolio.get("gold") or []):
        if not g.get("enabled", True):
            continue
        code = g["code"]
        source = g.get("source")
        if source == "sge":
            df = fetch_sge_gold_kline()
        elif source == "akshare":
            df = fetch_gold_etf_kline(code)
        else:
            df = fetch_sge_gold_kline()
        if df is not None:
            n = save_kline(code, df)
            results["gold"].append({"code": code, "name": g.get("name"), "rows": n})
        else:
            results["errors"].append(f"gold {code} failed")

    df = fetch_benchmark_index("000300")
    if df is not None:
        rows = [("INDEX_000300", r["date"], None, r["close"], None, None, None, None, None)
                for _, r in df.iterrows()]
        with conn() as c:
            c.executemany(
                """INSERT OR REPLACE INTO kline
                   (code,date,open,close,high,low,volume,amount,turnover)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM kline WHERE code='INDEX_000300' AND date < ?", (cutoff,))
        results["benchmark"] = len(rows)

    logger.info(f"refresh done: {results}")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(refresh_all())