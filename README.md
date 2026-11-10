# 小小投资家 · 股票+黄金模拟盘

> 给孩子的财商教育工具：真实行情数据 + 完全虚拟交易，培养投资观念、风险意识与长期思维。

## ✨ 功能

- 📈 **行情展示** - 股票（A 股）+ 黄金（Au99.99、黄金 ETF），90 天滚动数据
- 📊 **K 线图** - ECharts 蜡烛图 + MA5/MA10/MA20 均线
- 💼 **账户系统** - 现金 / 持仓 / 总资产 / 总收益 / 累计手续费
- 💸 **买入卖出** - A 股按整手（100 股）交易，黄金按克/份交易
- 💰 **手续费** - 万分之一，最低 1 元（自动提示教育文案）
- 📜 **交易记录** - 完整保存所有操作，支持备注
- 💸 **转入转出** - 模拟家长奖励 / 奖学金
- 📈 **收益曲线** - 与沪深 300 对比，让孩子学会「跑赢 / 跑输大盘」
- 🎓 **财商小课堂** - 关键时点弹出教育提示
- ⚙️ **配置驱动** - 修改 `config.yaml` 即可增减标的，自动热加载

## 🚀 启动

```bash
cd /root/.openclaw/workspace/stock
./start.sh
```

浏览器访问 http://localhost:8088

首次启动会自动拉取所有股票和黄金的 90 天行情（约 30 秒）。

## 📁 项目结构

```
stock/
├── start.sh           # 启动脚本
├── requirements.txt   # 依赖
├── config.yaml        # 标的 + 账户 + 风控 + 教育配置
├── config_loader.py   # 配置加载（支持热更新）
├── database.py        # SQLite 数据层
├── data_fetcher.py    # 行情拉取（akshare）
├── trader.py          # 交易引擎（手续费 / 持仓 / 收益）
├── app.py             # FastAPI 后端
├── data/
│   └── stock.db       # SQLite 数据库（自动生成）
├── static/
│   ├── style.css      # 前端样式
│   └── app.js         # 前端 Vue3 逻辑
└── templates/
    └── index.html     # 单页应用
```

## ⚙️ 配置说明（`config.yaml`）

### 增加股票
```yaml
portfolio:
  stocks:
    - code: "600519"     # A 股代码
      name: "贵州茅台"
      market: "SH"
      category: "消费"
      difficulty: "简单"
      tag: "孩子都听过"
      enabled: true
```

### 增加黄金
```yaml
portfolio:
  gold:
    - code: "Au99.99"
      name: "上海金 Au99.99"
      source: "sge"        # sge=上金所基准 / akshare=ETF历史
      enabled: true
```

### 风控
```yaml
risk:
  daily_buy_limit: 200000    # 单日买入限额（元），0=不限
  daily_trade_limit: 10      # 单日交易笔数上限，0=不限
```

### 手续费
```yaml
account:
  fee_rate: 0.0001          # 万分之一
  fee_min: 1.0              # 最低 1 元
```

### 教育文案
```yaml
education:
  popups:
    - trigger: "first_buy"        # 触发场景（前端可手动弹出）
      title: "🎓 第一次买入！"
      content: "..."
```

## 🔌 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/portfolio` | GET | 配置中的所有标的 |
| `/api/kline?code=xxx&days=90` | GET | K 线数据 |
| `/api/quote?code=xxx` | GET | 最新报价 |
| `/api/refresh` | POST | 手动刷新行情 |
| `/api/account` | GET | 账户总览（含持仓） |
| `/api/positions` | GET | 持仓列表 |
| `/api/buy` | POST | 买入 `{code, shares, note}` |
| `/api/sell` | POST | 卖出 `{code, shares, note}` |
| `/api/transfer` | POST | 转入转出 `{direction: in/out, amount, note}` |
| `/api/trades?limit=50` | GET | 交易记录 |
| `/api/cash_log?limit=20` | GET | 转入转出流水 |
| `/api/pnl/curve?days=30` | GET | 收益曲线 |
| `/api/pnl/snapshot` | POST | 立即生成今日快照 |
| `/api/edu/popups` | GET | 教育提示文案 |

## 🛠 技术栈

- 后端：Python 3.12 + FastAPI 0.141 + SQLite
- 数据：AKShare（带多接口 fallback）
- 前端：Vue 3 + ECharts 5（CDN）
- 调度：APScheduler（每日 16:30 自动拉数据）

## 📝 开发提示

- 数据来源在 `data_fetcher.py`，目前 A 股主用新浪 stock_zh_a_daily，东方财富 stock_zh_a_hist 兜底
- 网络不通时，A 股 K 线可能拉不到 — 检查 akshare 版本（推荐 ≥ 1.16）
- 黄金 ETF 518880 走东方财富 ak 接口，如不通会自动 fallback 到上金所基准金价
- 修改 `config.yaml` 后页面刷新即可生效

## 🐷 给孩子的话

> "投资不是赌博，是用时间和耐心换取复利的力量。"