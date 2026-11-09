# 📈 小小投资家 (Stock)

A 股行情与投研分析服务。FastAPI 后端,提供实时/历史行情、板块数据、个股诊断与策略筛选。

## 技术栈
- Python 3 + FastAPI + uvicorn
- 数据源: akshare 等公开财经接口

## 运行
```bash
pip install -r requirements.txt
python3 app.py    # 默认监听 0.0.0.0:8088
```

## 目录
| 路径 | 说明 |
|---|---|
| `app.py` | FastAPI 服务与 API 路由 |
| `config_loader.py` / `config.yaml` | 运行配置(阈值、开关等) |
| `database.py` | SQLite 缓存层 |
| `data_fetcher.py` | 行情数据抓取与清洗 |
| `services/` | 业务服务模块 |

## 部署
systemd 托管,端口 8088;经 Caddy 反代暴露在 `/stock/`。

> ⚠️ `config.yaml` 为运行配置,不含密钥;若需接入付费数据源请用环境变量注入。
