/* 小小投资家 - 前端逻辑 */
const { createApp, ref, computed, onMounted, watch, nextTick } = Vue;

createApp({
  setup() {
    const tabs = [
      { key: 'market', label: '行情' },
      { key: 'kline', label: 'K线详情' },
      { key: 'positions', label: '持仓' },
      { key: 'pnl', label: '收益曲线' },
      { key: 'history', label: '交易记录' },
    ];
    const tab = ref('market');

    const acc = ref({ total_assets: 0, cash: 0, market_value: 0,
                     total_profit: 0, total_profit_rate: 0, total_fee: 0, positions: [] });
    const portfolio = ref({ items: [] });
    const currentKline = ref({ code: '', name: '', data: [], latest_price: null, latest_date: '', market_cap: null });
    const buyShares = ref(100);
    const sellShares = ref(100);
    const tradeNote = ref('');
    const myPosition = ref(null);

    const trades = ref([]);
    const pnlCurve = ref([]);
    const pnlDays = ref(30);
    const search = ref('');
    const sectorFilter = ref('all');

    const showTransfer = ref(false);
    const transfer = ref({ direction: 'in', amount: 100, note: '' });

    const toast = ref({ msg: '', type: 'success', edu: '' });
    let toastTimer;

    function showToast(msg, type = 'success', edu = '') {
      toast.value = { msg, type, edu };
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toast.value = { msg: '', type: '', edu: '' }, 5000);
    }

    const filteredPortfolio = computed(() => {
      const items = portfolio.value.items || [];
      let r = items;
      if (sectorFilter.value !== 'all') {
        if (sectorFilter.value === 'gold') {
          r = items.filter(i => i.asset_type === 'gold');
        } else {
          r = items.filter(i => i.sector === sectorFilter.value);
        }
      }
      if (search.value) {
        const q = search.value.toLowerCase();
        r = r.filter(it => (it.name + it.code).toLowerCase().includes(q));
      }
      return r;
    });

    const profitClass = computed(() => {
      if (acc.value.total_profit > 0) return 'profit-pos';
      if (acc.value.total_profit < 0) return 'profit-neg';
      return '';
    });

    function fmt(n, digits = 2) {
      if (n === null || n === undefined || isNaN(n)) return '-';
      return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }
    function pct(n) {
      if (n === null || n === undefined || isNaN(n)) return '-';
      const v = (n * 100).toFixed(2);
      return (n >= 0 ? '+' : '') + v + '%';
    }
    function calcFee(amount) {
      if (!amount || amount <= 0) return 0;
      return Math.max(amount * 0.0001, 1.0).toFixed(2);
    }

    async function api(path, opts = {}) {
      const r = await fetch(path, opts);
      if (!r.ok) {
        const e = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(e.detail || '请求失败');
      }
      return r.json();
    }

    async function loadAccount() {
      acc.value = await api('/stock/api/account');
      // 同步持仓到当前标的
      if (currentKline.value.code) {
        const p = acc.value.positions.find(p => p.code === currentKline.value.code);
        myPosition.value = p || null;
        if (p && sellShares.value > p.shares) sellShares.value = Math.floor(p.shares) || 1;
      }
    }

    async function loadPortfolio() {
      const r = await api('/stock/api/portfolio');
      // 重置每只的 quote 状态，避免新 item 还显示旧 price
      for (const it of r.items) {
        it._quoteState = 'loading';
        it._price = null;
        it._change = 0;
        it._pct = 0;
      }
      portfolio.value = r;
      // 加载每只的实时价
      for (const it of r.items) {
        loadQuote(it);
      }
    }

    async function loadQuote(item) {
      item._quoteState = 'loading';
      try {
        const q = await api(`/stock/api/quote?code=${item.code}`);
        item._price = q.price;
        item._quoteState = 'priced';
        // 拿前一天收盘算涨跌
        try {
          const r = await api(`/stock/api/kline?code=${item.code}&days=2`);
          const data = r.data || [];
          if (data.length >= 2) {
            const prev = data[data.length - 2].close;
            const change = q.price - prev;
            item._change = change;
            item._pct = (change / prev) * 100;
          } else {
            item._change = 0;
            item._pct = 0;
          }
          item._quoteState = 'ok';
        } catch (ke) {
          item._change = 0;
          item._pct = 0;
          item._quoteState = 'ok';
        }
      } catch (e) {
        item._quoteState = 'fail';
      }
    }

    async function openKline(item) {
      tab.value = 'kline';
      currentKline.value = { code: item.code, name: item.name, data: [], latest_price: null, latest_date: '', market_cap: null };
      myPosition.value = null;
      try {
        const r = await api(`/stock/api/kline?code=${item.code}&days=90`);
        currentKline.value = { code: item.code, name: item.name, ...r };
        await nextTick();
        drawKline();
        // 同步持仓
        await loadAccount();
      } catch (e) {
        showToast(e.message, 'error');
      }
    }

    let kChart;
    function drawKline() {
      const el = document.getElementById('kline-chart');
      if (!el) return;
      if (!kChart) kChart = echarts.init(el);
      const data = currentKline.value.data || [];
      const dates = data.map(d => d.date);
      // ECharts candlestick: [open, close, low, high]
      const ohlc = data.map(d => [d.open, d.close, d.low, d.high]);
      const closes = data.map(d => d.close);
      const ma = (n) => {
        const r = [];
        for (let i = 0; i < closes.length; i++) {
          if (i < n - 1) { r.push('-'); continue; }
          let s = 0;
          for (let j = i - n + 1; j <= i; j++) s += closes[j];
          r.push((s / n).toFixed(2));
        }
        return r;
      };
      kChart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['K线', 'MA5', 'MA10', 'MA20'], top: 0 },
        grid: { left: 50, right: 20, top: 30, bottom: 30 },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
        yAxis: { scale: true },
        dataZoom: [{ type: 'inside', start: 50, end: 100 }, { type: 'slider', height: 20, bottom: 0 }],
        series: [
          {
            name: 'K线', type: 'candlestick', data: ohlc,
            itemStyle: {
              color: '#e85d52', color0: '#2db26b',
              borderColor: '#e85d52', borderColor0: '#2db26b',
            },
          },
          { name: 'MA5', type: 'line', data: ma(5), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#ff9800' } },
          { name: 'MA10', type: 'line', data: ma(10), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#2196f3' } },
          { name: 'MA20', type: 'line', data: ma(20), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#9c27b0' } },
        ],
      });
    }

    async function doBuy() {
      if (!buyShares.value || buyShares.value <= 0) {
        showToast('请输入数量', 'error'); return;
      }
      try {
        const r = await api('/stock/api/buy', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: currentKline.value.code, shares: buyShares.value, note: tradeNote.value }),
        });
        showToast(`✅ 买入成功 ${r.shares} ${r.name} @¥${fmt(r.price)}`, 'success',
          '万分之一手续费已扣除。记住: 买入前要问自己为什么!');
        tradeNote.value = '';
        await loadAccount();
        // 刷新当前K线最新价
        const q = await api(`/stock/api/quote?code=${currentKline.value.code}`);
        currentKline.value.latest_price = q.price;
      } catch (e) {
        showToast('❌ ' + e.message, 'error',
          e.message.includes('现金不足') ? '💡 教育提示: 现金不够, 可以先在「转入/转出」加钱(模拟家长奖励)' : '');
      }
    }

    async function doSell() {
      if (!sellShares.value || sellShares.value <= 0) {
        showToast('请输入数量', 'error'); return;
      }
      try {
        const r = await api('/stock/api/sell', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: currentKline.value.code, shares: sellShares.value, note: tradeNote.value }),
        });
        showToast(`✅ 卖出成功 ${r.shares} ${r.name} @¥${fmt(r.price)}`, 'success');
        tradeNote.value = '';
        await loadAccount();
      } catch (e) {
        showToast('❌ ' + e.message, 'error');
      }
    }

    async function doTransfer() {
      if (!transfer.value.amount || transfer.value.amount <= 0) {
        showToast('请输入金额', 'error'); return;
      }
      try {
        const r = await api('/stock/api/transfer', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(transfer.value),
        });
        showToast(`✅ ${transfer.value.direction === 'in' ? '转入' : '转出'} ¥${fmt(r.amount)}`);
        transfer.value.note = '';
        showTransfer.value = false;
        await loadAccount();
      } catch (e) {
        showToast('❌ ' + e.message, 'error');
      }
    }

    async function refreshData() {
      showToast('🔄 后台拉数据中...');
      try { await api('/stock/api/refresh', { method: 'POST' }); } catch (e) {}
      setTimeout(async () => {
        await loadPortfolio();
        await loadAccount();
        showToast('✅ 数据已刷新');
      }, 8000);
    }

    async function loadTrades() {
      const r = await api('/stock/api/trades');
      trades.value = r.trades;
    }

    async function loadPnl(days) {
      pnlDays.value = days;
      const r = await api(`/stock/api/pnl?days=${days}`);
      pnlCurve.value = r.curve;
      await nextTick();
      drawPnl();
    }

    let pChart;
    function drawPnl() {
      const el = document.getElementById('pnl-chart');
      if (!el) return;
      if (!pChart) pChart = echarts.init(el);
      const data = pnlCurve.value;
      if (!data.length) return;
      const dates = data.map(d => d.date);
      pChart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['我的账户', '沪深300'], top: 0 },
        grid: { left: 60, right: 20, top: 30, bottom: 30 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', scale: true },
        series: [
          { name: '我的账户', type: 'line', data: data.map(d => d.account_norm), smooth: true, areaStyle: { opacity: 0.2 }, lineStyle: { width: 2, color: '#ff7a59' } },
          { name: '沪深300', type: 'line', data: data.map(d => d.base_norm), smooth: true, lineStyle: { width: 1, color: '#888', type: 'dashed' } },
        ],
      });
    }

    async function snapshotNow() {
      try {
        await api('/stock/api/pnl/snapshot', { method: 'POST' });
        showToast('📸 已生成今日收益快照');
        await loadPnl(pnlDays.value);
        await loadAccount();
      } catch (e) { showToast('❌ ' + e.message, 'error'); }
    }

    watch(tab, async (v) => {
      if (v === 'positions' || v === 'market') await loadAccount();
      if (v === 'history') await loadTrades();
      if (v === 'pnl') await loadPnl(pnlDays.value);
    });

    onMounted(async () => {
      await loadPortfolio();
      await loadAccount();
      // 每 30s 刷新账户
      setInterval(loadAccount, 30000);
    });

    return {
      tabs, tab, acc, portfolio, currentKline, buyShares, sellShares, tradeNote, myPosition,
      trades, pnlCurve, pnlDays, search, sectorFilter,
      showTransfer, transfer, toast,
      filteredPortfolio, profitClass,
      fmt, pct, calcFee,
      openKline, doBuy, doSell, doTransfer, refreshData, loadPnl, snapshotNow,
    };
  }
}).mount('#app');