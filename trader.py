"""
交易引擎
- 买入: 按当前价成交 (模拟盘), 扣手续费, 更新持仓和账户
- 卖出: 同上
- 转入转出: 调账户余额
- 每日收盘后: 生成 daily_pnl 快照
"""
from __future__ import annotations

import logging
from datetime import datetime, date as _date
from typing import Dict, Optional, Tuple, Any

from config_loader import config
from database import conn, dict_rows, init_db

logger = logging.getLogger("stock.trader")


# ============== 工具 ==============
def calc_fee(amount: float) -> float:
    """万分之一, 最低 1 元"""
    rate = config.get("account.fee_rate", 0.0001)
    fee_min = config.get("account.fee_min", 1.0)
    fee = amount * rate
    return round(max(fee, fee_min), 2)


def get_current_price(code: str) -> Optional[float]:
    """取标的最新一天的收盘价"""
    with conn() as c:
        row = c.execute(
            "SELECT close FROM kline WHERE code=? ORDER BY date DESC LIMIT 1", (code,)
        ).fetchone()
        return row["close"] if row else None


def get_latest_market_cap(code: str) -> Optional[float]:
    """取最新一天的市值"""
    with conn() as c:
        row = c.execute(
            "SELECT market_cap FROM kline WHERE code=? AND market_cap IS NOT NULL ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
        return row["market_cap"] if row else None


def get_position(code: str) -> Optional[Dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM positions WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def get_account() -> Dict:
    with conn() as c:
        row = c.execute("SELECT * FROM account WHERE id=1").fetchone()
        return dict(row) if row else {}


# ============== 资产计算 ==============
def calc_position_market_value(position: Dict) -> Dict:
    """计算持仓市值/盈亏"""
    code = position["code"]
    price = get_current_price(code) or position["cost_price"]
    mv = round(position["shares"] * price, 2)
    cost = round(position["shares"] * position["cost_price"], 2)
    profit = round(mv - cost, 2)
    profit_rate = round(profit / cost, 4) if cost > 0 else 0
    return {
        **position,
        "current_price": price,
        "market_value": mv,
        "cost_total": cost,
        "profit": profit,
        "profit_rate": profit_rate,
    }


def get_all_positions() -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM positions").fetchall()
    return [calc_position_market_value(dict(r)) for r in rows]


def calc_total_assets() -> Dict[str, Any]:
    """总资产/总收益"""
    init_db()
    acc = get_account()
    cash = acc.get("cash", 0)
    positions = get_all_positions()
    market_value = round(sum(p["market_value"] for p in positions), 2)
    total = round(cash + market_value, 2)
    initial = acc.get("initial_cash", 0)
    profit = round(total - initial, 2)
    profit_rate = round(profit / initial, 4) if initial > 0 else 0
    return {
        "cash": cash,
        "market_value": market_value,
        "total_assets": total,
        "initial_cash": initial,
        "total_profit": profit,
        "total_profit_rate": profit_rate,
        "total_fee": acc.get("total_fee", 0),
        "positions": positions,
    }


# ============== 买卖 ==============
def buy(code: str, name: str, asset_type: str, shares: float, price: Optional[float] = None, note: str = "") -> Dict:
    """
    买入
    - code: 600519
    - name: 贵州茅台
    - asset_type: stock / gold
    - shares: 股数(A股100股一手) 或 黄金克数/份数
    - price: None则按当前价
    """
    init_db()
    if shares <= 0:
        raise ValueError("数量必须 > 0")
    if price is None:
        price = get_current_price(code)
    if price is None:
        raise ValueError(f"无法获取 {code} 当前价格")
    amount = round(shares * price, 2)
    fee = calc_fee(amount)
    total_cost = round(amount + fee, 2)

    acc = get_account()
    if acc["cash"] < total_cost:
        raise ValueError(f"现金不足: 需要 {total_cost}, 只有 {acc['cash']:.2f}")

    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        # 扣现金
        new_cash = round(acc["cash"] - total_cost, 2)
        c.execute("UPDATE account SET cash=?, total_fee=total_fee+?, updated_at=? WHERE id=1",
                  (new_cash, fee, now))
        # 更新持仓（加权平均成本）
        pos = c.execute("SELECT * FROM positions WHERE code=?", (code,)).fetchone()
        if pos:
            old_shares = pos["shares"]
            old_cost = pos["cost_price"]
            new_shares = round(old_shares + shares, 4)
            new_cost = round((old_shares * old_cost + shares * price) / new_shares, 4)
            c.execute(
                "UPDATE positions SET shares=?, cost_price=?, updated_at=? WHERE code=?",
                (new_shares, new_cost, now, code),
            )
        else:
            c.execute(
                "INSERT INTO positions (code,name,asset_type,shares,cost_price,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (code, name, asset_type, shares, price, now, now),
            )
        # 记交易
        c.execute(
            """INSERT INTO trades (code,name,asset_type,side,price,shares,amount,fee,cash_after,note,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (code, name, asset_type, "buy", price, shares, amount, fee, new_cash, note, now),
        )

    return {
        "ok": True,
        "code": code, "name": name, "side": "buy",
        "price": price, "shares": shares, "amount": amount,
        "fee": fee, "cash_after": new_cash,
    }


def sell(code: str, shares: float, price: Optional[float] = None, note: str = "") -> Dict:
    """卖出"""
    init_db()
    if shares <= 0:
        raise ValueError("数量必须 > 0")
    if price is None:
        price = get_current_price(code)
    if price is None:
        raise ValueError(f"无法获取 {code} 当前价格")

    pos = get_position(code)
    if not pos:
        raise ValueError(f"未持有 {code}")
    if shares > pos["shares"]:
        raise ValueError(f"持有不足: 持有 {pos['shares']}, 拟卖 {shares}")

    amount = round(shares * price, 2)
    fee = calc_fee(amount)
    proceeds = round(amount - fee, 2)

    acc = get_account()
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        new_cash = round(acc["cash"] + proceeds, 2)
        c.execute("UPDATE account SET cash=?, total_fee=total_fee+?, updated_at=? WHERE id=1",
                  (new_cash, fee, now))
        new_shares = round(pos["shares"] - shares, 4)
        if new_shares <= 0:
            c.execute("DELETE FROM positions WHERE code=?", (code,))
        else:
            c.execute("UPDATE positions SET shares=?, updated_at=? WHERE code=?",
                      (new_shares, now, code))
        c.execute(
            """INSERT INTO trades (code,name,asset_type,side,price,shares,amount,fee,cash_after,note,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (code, pos["name"], pos["asset_type"], "sell", price, shares, amount, fee, new_cash, note, now),
        )

    return {
        "ok": True,
        "code": code, "name": pos["name"], "side": "sell",
        "price": price, "shares": shares, "amount": amount,
        "fee": fee, "cash_after": new_cash,
    }


# ============== 转入转出 ==============
def cash_transfer(direction: str, amount: float, note: str = "") -> Dict:
    """direction: in / out.
    转入转出会同时调整 initial_cash，这样总收益只反映投资盈亏，不被转入干扰。
    """
    if direction not in ("in", "out"):
        raise ValueError("direction 必须是 in 或 out")
    if amount <= 0:
        raise ValueError("金额必须 > 0")
    init_db()
    acc = get_account()
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        if direction == "in":
            new_cash = round(acc["cash"] + amount, 2)
            new_initial = round(acc["initial_cash"] + amount, 2)
        else:
            if acc["cash"] < amount:
                raise ValueError(f"现金不足: 有 {acc['cash']:.2f}, 拟转出 {amount}")
            new_cash = round(acc["cash"] - amount, 2)
            new_initial = round(acc["initial_cash"] - amount, 2)
        c.execute("UPDATE account SET cash=?, initial_cash=?, updated_at=? WHERE id=1",
                  (new_cash, new_initial, now))
        c.execute("INSERT INTO cash_log (type, amount, note, created_at) VALUES (?,?,?,?)",
                  (direction, amount, note, now))
    return {"ok": True, "type": direction, "amount": amount, "cash_after": new_cash}


# ============== 收益快照 ==============
def snapshot_daily_pnl():
    """把今日的总资产/收益记入 daily_pnl (用于画收益曲线)"""
    init_db()
    total = calc_total_assets()
    today = datetime.now().strftime("%Y-%m-%d")
    # 取基准 (沪深300今日)
    base_close = get_current_price("INDEX_000300")
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO daily_pnl
               (date,total_assets,cash,market_value,profit,profit_rate,base_index)
               VALUES (?,?,?,?,?,?,?)""",
            (
                today,
                total["total_assets"], total["cash"], total["market_value"],
                total["total_profit"], total["total_profit_rate"],
                base_close,
            ),
        )
    return total


def get_pnl_curve(days: int = 90) -> list:
    """获取收益曲线 (含基准归一化)"""
    from datetime import timedelta
    with conn() as c:
        rows = c.execute(
            "SELECT date, total_assets, profit, profit_rate, base_index "
            "FROM daily_pnl ORDER BY date DESC LIMIT ?",
            (days,),
        ).fetchall()
    rows = [dict(r) for r in rows][::-1]
    if not rows:
        return []
    # 基准归一化 (从 100 开始)
    first_base = rows[0]["base_index"]
    for r in rows:
        r["base_norm"] = round(r["base_index"] / first_base * 100, 2) if first_base else 100
        r["account_norm"] = round(r["total_assets"] / (r["total_assets"] - r["profit"]) * 100, 2) if (r["total_assets"] - r["profit"]) > 0 else 100
    return rows


# ============== 交易记录 ==============
def get_trades(limit: int = 50) -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_cash_log(limit: int = 20) -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM cash_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    print(calc_total_assets())