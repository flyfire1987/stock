"""
FastAPI 后端 - 所有路由
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, str(Path(__file__).parent))

from config_loader import config
from database import init_db, conn
import data_fetcher
import trader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stock.app")

# ============== 调度器 ==============
scheduler = BackgroundScheduler()


def daily_refresh():
    """每日 16:30 拉数据 + 收益快照"""
    logger.info("scheduler: refresh data + pnl")
    try:
        data_fetcher.refresh_all()
        trader.snapshot_daily_pnl()
    except Exception as e:
        logger.exception(f"daily refresh failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 启动时立即拉一次数据 (后台线程, 不阻塞)
    import threading
    threading.Thread(target=data_fetcher.refresh_all, daemon=True).start()
    # 调度每天 16:30 刷新
    scheduler.add_job(daily_refresh, "cron", hour=16, minute=30,
                      id="daily_refresh", replace_existing=True)
    scheduler.start()
    logger.info("scheduler started, daily_refresh at 16:30")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=config.get("app.title", "Stock Sim"), lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# ============== 静态/首页 ==============
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# 挂载静态资源 (顺序必须在路由前)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse({}, status_code=204)


# ============== 行情 ==============
@app.get("/api/portfolio")
def get_portfolio():
    """返回配置中的所有标的（含元信息）"""
    cfg = config.all
    portfolio = cfg.get("portfolio", {})
    items = []
    for s in (portfolio.get("stocks") or []):
        if s.get("enabled", True):
            items.append({**s, "asset_type": "stock"})
    for g in (portfolio.get("gold") or []):
        if g.get("enabled", True):
            items.append({**g, "asset_type": "gold"})
    return {"items": items}


@app.get("/api/kline")
def get_kline(code: str, days: int = 90):
    """K线数据（最多 90 天）"""
    days = min(days, 90)
    with conn() as c:
        rows = c.execute(
            "SELECT date, open, close, high, low, volume, amount, turnover, market_cap "
            "FROM kline WHERE code=? ORDER BY date DESC LIMIT ?",
            (code, days),
        ).fetchall()
    rows = [dict(r) for r in rows][::-1]  # 升序
    if not rows:
        return {"code": code, "data": [], "latest_price": None,
                "market_cap": None, "msg": "暂无数据, 等待拉取..."}

    latest = rows[-1]
    return {
        "code": code,
        "data": rows,
        "latest_price": latest["close"],
        "latest_date": latest["date"],
        "market_cap": latest.get("market_cap"),
    }


@app.get("/api/quote")
def get_quote(code: str):
    """最新报价（用于下单时显示当前价）"""
    price = trader.get_current_price(code)
    cap = trader.get_latest_market_cap(code)
    if price is None:
        raise HTTPException(404, f"{code} 暂无行情")
    return {"code": code, "price": price, "market_cap": cap}


@app.post("/api/refresh")
def refresh_data():
    """手动触发刷新（不阻塞，前台跑）"""
    import threading
    threading.Thread(target=data_fetcher.refresh_all, daemon=True).start()
    return {"ok": True, "msg": "已后台启动拉数据"}


# ============== 账户/持仓 ==============
@app.get("/api/account")
def get_account():
    return trader.calc_total_assets()


@app.get("/api/positions")
def get_positions():
    return {"positions": trader.get_all_positions()}


# ============== 交易 ==============
class BuyReq(BaseModel):
    code: str
    shares: float
    note: str = ""


class SellReq(BaseModel):
    code: str
    shares: float
    note: str = ""


class TransferReq(BaseModel):
    direction: str  # in / out
    amount: float
    note: str = ""


@app.post("/api/buy")
def api_buy(req: BuyReq):
    """买入（按当前价成交）"""
    # 找标的信息
    info = None
    cfg = config.all
    for s in cfg.get("portfolio", {}).get("stocks", []):
        if s.get("code") == req.code:
            info = {**s, "asset_type": "stock"}
            break
    if not info:
        for g in cfg.get("portfolio", {}).get("gold", []):
            if g.get("code") == req.code:
                info = {**g, "asset_type": "gold"}
                break
    if not info:
        raise HTTPException(404, f"未在配置中找到 {req.code}")

    # 风控
    risk_check = _check_risk("buy", req.code, req.shares)
    if not risk_check["ok"]:
        raise HTTPException(400, risk_check["msg"])

    # 整手检查 (A股100股一手)
    if info.get("asset_type") == "stock" and info.get("market") in ("SH", "SZ"):
        if int(req.shares) != req.shares or int(req.shares) % 100 != 0:
            raise HTTPException(400, "A股必须整手(100的倍数)")

    try:
        result = trader.buy(req.code, info["name"], info["asset_type"], req.shares, note=req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/api/sell")
def api_sell(req: SellReq):
    risk_check = _check_risk("sell", req.code, req.shares)
    if not risk_check["ok"]:
        raise HTTPException(400, risk_check["msg"])
    try:
        result = trader.sell(req.code, req.shares, note=req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/api/transfer")
def api_transfer(req: TransferReq):
    try:
        return trader.cash_transfer(req.direction, req.amount, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _check_risk(side: str, code: str, shares: float) -> dict:
    """风控: 单日买入限额 + 单日交易笔数"""
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        # 当日该标的已交易笔数
        n_today = c.execute(
            "SELECT COUNT(*) c FROM trades WHERE created_at LIKE ?", (today + "%",)
        ).fetchone()["c"]
        # 当日买入金额
        if side == "buy":
            buy_amt = c.execute(
                "SELECT COALESCE(SUM(amount+fee),0) s FROM trades "
                "WHERE side='buy' AND created_at LIKE ?", (today + "%",)
            ).fetchone()["s"]
            limit = config.get("risk.daily_buy_limit", 0)
            if limit > 0 and buy_amt >= limit:
                return {"ok": False, "msg": f"今日买入已达限额 {limit} 元"}
            # 估算本次金额
            price = trader.get_current_price(code) or 0
            if limit > 0 and (buy_amt + shares * price) > limit:
                return {"ok": False, "msg": f"今日买入将超限额 {limit} 元"}
        n_limit = config.get("risk.daily_trade_limit", 0)
        if n_limit > 0 and n_today >= n_limit:
            return {"ok": False, "msg": f"今日交易笔数已达上限 {n_limit}"}
    return {"ok": True}


# ============== 交易记录 ==============
@app.get("/api/trades")
def api_trades(limit: int = 50):
    return {"trades": trader.get_trades(limit)}


@app.get("/api/cash_log")
def api_cash_log(limit: int = 20):
    return {"log": trader.get_cash_log(limit)}


# ============== 收益曲线 ==============
@app.get("/api/pnl/curve")
def api_pnl_curve(days: int = 90):
    return {"curve": trader.get_pnl_curve(days)}


@app.post("/api/pnl/snapshot")
def api_pnl_snapshot():
    """手动触发今日快照"""
    trader.snapshot_daily_pnl()
    return {"ok": True}


# ============== 教育提示 ==============
@app.get("/api/edu/popups")
def get_edu_popups():
    return {"popups": config.get("education.popups", [])}


# ============== 启动 ==============
if __name__ == "__main__":
    import uvicorn
    host = config.get("app.host", "0.0.0.0")
    port = config.get("app.port", 8088)
    logger.info(f"start at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", workers=1)