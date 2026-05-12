/* Dashboard investments - app.js */

var S = {
  portfolio:   {},
  prices:      {},
  targets:     {},
  usdIls:      3.65,
  activeTab:   'מניות',
  chartTicker: null,
  chartRange:  '1mo',
  pieChart:    null,
  formOpen:    false
};

var TV_SYMBOLS = {
  'NFLX':    'NASDAQ:NFLX',
  'ORCL':    'NYSE:ORCL',
  'NVDA':    'NASDAQ:NVDA',
  'GOOG':    'NASDAQ:GOOG',
  'GOOGL':   'NASDAQ:GOOGL',
  'AAPL':    'NASDAQ:AAPL',
  'MSFT':    'NASDAQ:MSFT',
  'AMZN':    'NASDAQ:AMZN',
  'META':    'NASDAQ:META',
  'TSLA':    'NASDAQ:TSLA',
  'VOO':     'AMEX:VOO',
  'QQQ':     'NASDAQ:QQQ',
  'SPY':     'AMEX:SPY',
  'BTC-USD': 'BINANCE:BTCUSDT',
  'ETH-USD': 'BINANCE:ETHUSDT'
};

var TV_RANGES = {
  '7d':  '5D',
  '1mo': '1M',
  '3mo': '3M',
  '1y':  '12M'
};

function toTvSymbol(ticker) {
  return TV_SYMBOLS[ticker] || ticker;
}

var TAB_STOCKS  = 'מניות';
var TAB_SUMMARY = 'סיכום';
var CRYPTO_TABS = ['BTC', 'ETH'];

// --- Boot ---

document.addEventListener('DOMContentLoaded', function() {
  startClock();
  setupAutocomplete();
  init();
});

async function init() {
  setStatus('loading');
  try {
    var results = await Promise.all([
      apiFetch('/api/portfolio'),
      apiFetch('/api/rate/usdils')
    ]);
    S.portfolio = results[0];
    S.usdIls = results[1].rate || 3.65;
    await refreshPrices();
    switchTab(TAB_STOCKS, false);
    setStatus('live');
  } catch(e) {
    setStatus('error');
    console.error(e);
  }
  setInterval(refreshAll, 60000);
}

async function refreshAll() {
  try {
    var rd = await apiFetch('/api/rate/usdils');
    if (rd && rd.rate) S.usdIls = rd.rate;
  } catch(e) {}
  await refreshPrices();
  renderActiveTab();
  renderSummaryCards();
}

async function refreshPrices() {
  var tickers = getAllTickers();
  var prev = JSON.parse(JSON.stringify(S.prices));
  var results = await Promise.allSettled(
    tickers.map(function(t) {
      return apiFetch('/api/price/' + encodeURIComponent(t))
        .then(function(d) { return Object.assign({ ticker: t }, d); });
    })
  );
  results.forEach(function(r) {
    if (r.status === 'fulfilled' && r.value && r.value.price) {
      var ticker = r.value.ticker;
      S.prices[ticker] = { price: r.value.price, change_pct: r.value.change_pct, prev_close: r.value.prev_close };
    }
  });
  renderSummaryCards();
  flashChangedPrices(prev);
  setStatus('live');
}

// --- API ---

async function apiFetch(url, opts) {
  opts = opts || {};
  var options = {};
  Object.keys(opts).forEach(function(k) { options[k] = opts[k]; });
  if (opts.json !== undefined) {
    options.method = opts.method || 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(opts.json);
    delete options.json;
  }
  var r = await fetch(url, options);
  if (!r.ok) throw new Error(r.status + ' ' + url);
  return r.json();
}

// --- Status ---

function setStatus(state) {
  var dot = document.getElementById('status-dot');
  var txt = document.getElementById('status-text');
  dot.className = 'status-dot ' + state;
  if (state === 'live')    txt.textContent = 'עודכן ' + new Date().toLocaleTimeString('he-IL');
  if (state === 'loading') txt.textContent = 'טוען...';
  if (state === 'error')   txt.textContent = 'שגיאת חיבור';
}

// --- Clock ---

function startClock() {
  var el = document.getElementById('clock');
  function tick() { el.textContent = new Date().toLocaleString('he-IL'); }
  tick();
  setInterval(tick, 1000);
}

// --- Summary cards ---

function renderSummaryCards() {
  var totalInvIls = 0, currentIls = 0;
  getAllInvestments().forEach(function(inv) {
    totalInvIls += calcInvestedIls(inv);
    var pd = S.prices[inv.ticker];
    if (pd && pd.price) currentIls += calcCurrentIls(inv, pd.price);
  });
  var investedIls = totalInvIls;
  var pnl = currentIls - investedIls;
  var ret = investedIls ? pnl / investedIls * 100 : 0;

  setEl('stat-invested', '₪' + fmt0(investedIls));
  setEl('stat-current',  '₪' + fmt0(currentIls));

  var pnlEl = document.getElementById('stat-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '₪' + fmt0(pnl);
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

  var retEl = document.getElementById('stat-return');
  retEl.textContent = (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
  retEl.style.color = ret >= 0 ? 'var(--green)' : 'var(--red)';

  setEl('stat-rate', '₪' + S.usdIls.toFixed(3));
}

// --- Tabs ---

function switchTab(tab, scroll) {
  if (scroll === undefined) scroll = true;
  S.activeTab = tab;
  document.querySelectorAll('.tab').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  renderActiveTab();
  if (tab === TAB_STOCKS) fetchTargets();

  if (tab !== TAB_STOCKS && tab !== TAB_SUMMARY) {
    var autoTicker = tab === 'VOO' ? 'VOO'
                   : tab === 'QQQ' ? 'QQQ'
                   : tab === 'BTC' ? 'BTC-USD'
                   : tab === 'ETH' ? 'ETH-USD'
                   : null;
    if (autoTicker) onRowClick(autoTicker);
  } else {
    closeChart();
  }

  if (scroll) {
    var el = document.getElementById('main-content');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function renderActiveTab() {
  var el = document.getElementById('main-content');
  if (S.pieChart) { S.pieChart.destroy(); S.pieChart = null; }
  if (S.activeTab === TAB_SUMMARY) {
    el.innerHTML = renderSummaryTab();
    renderPieChart();
  } else {
    var investments = S.portfolio[S.activeTab] || [];
    el.innerHTML = renderTable(investments, S.activeTab === TAB_STOCKS);
  }
}

// --- Table ---

function renderTable(investments, isStocks) {
  if (!investments.length) {
    return '<div class="spinner">אין השקעות בקטגוריה זו &mdash; הוסף אחת למעלה.</div>';
  }

  var invUsd = 0, invIls = 0, curIls = 0;
  var colCount = 10 + (isStocks ? 1 : 0);

  function buildRow(inv) {
    var pd = S.prices[inv.ticker];
    var p  = pd ? pd.price : null;
    var cur = p ? calcCurrentIls(inv, p) : null;
    var pct = p ? (p - inv.purchase_price) / inv.purchase_price * 100 : null;
    var pctClass = pct === null ? '' : pct >= 0 ? 'pos' : 'neg';
    var pctStr   = pct === null ? '...' : (pct >= 0 ? '+' : '') + pct.toFixed(3) + '%';
    var rowIls   = calcInvestedIls(inv);

    invUsd += inv.invested_usd;
    invIls += rowIls;
    if (cur) curIls += cur;

    var targetCell = isStocks
      ? '<td class="td-price">' + (S.targets[inv.ticker] != null ? '$' + fmt2(S.targets[inv.ticker]) : '...') + '</td>'
      : '';

    var catEsc = S.activeTab.replace(/'/g, "\\'");
    var idEsc  = inv.id.replace(/'/g, "\\'");

    return '<tr onclick="onRowClick(\'' + inv.ticker + '\')" data-id="' + inv.id + '">' +
      '<td class="td-ticker">' + inv.ticker + '</td>' +
      '<td class="td-name">' + (inv.name || '') + '</td>' +
      '<td>$' + fmt2(inv.invested_usd) + '</td>' +
      '<td>₪' + fmt0(rowIls) + '</td>' +
      '<td>' + fmtDate(inv.purchase_date) + '</td>' +
      '<td class="td-price">$' + fmtPrice(inv.purchase_price) + '</td>' +
      '<td class="td-price" id="pr-' + inv.id + '">' + (p ? '$' + fmtPrice(p) : '...') + '</td>' +
      '<td class="td-pct ' + pctClass + '" id="pct-' + inv.id + '">' + pctStr + '</td>' +
      '<td class="td-value" id="val-' + inv.id + '">' + (cur ? '₪' + fmt2(cur) : '...') + '</td>' +
      targetCell +
      '<td><button class="btn-del" onclick="delRow(event,\'' + catEsc + '\',\'' + idEsc + '\')">🗑</button></td>' +
      '</tr>';
  }

  var rows = investments.map(buildRow).join('');

  var totalPct   = invIls ? (curIls - invIls) / invIls * 100 : 0;
  var totalClass = totalPct >= 0 ? 'pos' : 'neg';
  var targetHeader = isStocks ? '<th>יעד אנליסטים ($)</th>' : '';
  var targetEmpty  = isStocks ? '<td>&mdash;</td>' : '';

  return '<div class="table-wrap"><table>' +
    '<thead><tr>' +
    '<th>מניה / נכס</th><th>שם</th>' +
    '<th>הושקע ($)</th><th>הושקע (₪)</th>' +
    '<th>תאריך קנייה</th><th>מחיר קנייה</th>' +
    '<th>מחיר עכשווי ⟳</th><th>%</th>' +
    '<th>שווי עכשווי (₪)</th>' +
    targetHeader +
    '<th></th>' +
    '</tr></thead>' +
    '<tbody>' + rows +
    '<tr class="tr-total">' +
    '<td colspan="2">סה"כ</td>' +
    '<td>$' + fmt2(invUsd) + '</td>' +
    '<td>₪' + fmt0(invIls) + '</td>' +
    '<td></td><td></td><td></td>' +
    '<td class="td-pct ' + totalClass + '">' + (totalPct >= 0 ? '+' : '') + totalPct.toFixed(3) + '%</td>' +
    '<td class="td-value">₪' + fmt2(curIls) + '</td>' +
    targetEmpty +
    '<td></td>' +
    '</tr>' +
    '</tbody></table></div>';
}

// --- Summary tab ---

function renderSummaryTab() {
  var displayCats = [
    { label: TAB_STOCKS, tabs: [TAB_STOCKS], clickTab: TAB_STOCKS },
    { label: 'VOO',      tabs: ['VOO'],       clickTab: 'VOO' },
    { label: 'QQQ',      tabs: ['QQQ'],       clickTab: 'QQQ' },
    { label: 'CRYPTO',   tabs: ['BTC','ETH'], clickTab: 'BTC' }
  ];
  var gInvIls = 0, gCurIls = 0;

  var rows = displayCats.map(function(cat) {
    var invs = [], invUsd = 0, invIls = 0, curIls = 0;
    cat.tabs.forEach(function(t) {
      (S.portfolio[t] || []).forEach(function(inv) {
        invs.push(inv);
        invUsd += inv.invested_usd;
        invIls += calcInvestedIls(inv);
        var pd = S.prices[inv.ticker];
        if (pd && pd.price) curIls += calcCurrentIls(inv, pd.price);
      });
    });
    gInvIls += invIls;
    gCurIls += curIls;
    var pct = invIls ? (curIls - invIls) / invIls * 100 : 0;
    var cls = pct >= 0 ? 'pos' : 'neg';
    return '<tr onclick="switchTab(\'' + cat.clickTab + '\')">' +
      '<td class="td-ticker">' + cat.label + '</td>' +
      '<td>' + invs.length + ' פוזיציות</td>' +
      '<td>$' + fmt2(invUsd) + '</td>' +
      '<td>₪' + fmt0(invIls) + '</td>' +
      '<td class="td-value">₪' + fmt2(curIls) + '</td>' +
      '<td class="td-pct ' + cls + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(3) + '%</td>' +
      '<td class="td-pct ' + cls + '">' + ((curIls - invIls) >= 0 ? '+' : '') + '₪' + fmt2(curIls - invIls) + '</td>' +
      '</tr>';
  }).join('');

  var gPct = gInvIls ? (gCurIls - gInvIls) / gInvIls * 100 : 0;
  var gCls = gPct >= 0 ? 'pos' : 'neg';
  var pnlColor = gPct >= 0 ? 'var(--green)' : 'var(--red)';

  return '<div class="summary-header-cards">' +
    '<div class="sum-card"><div class="sum-card-label">סה"כ הושקע</div><div class="sum-card-value">₪' + fmt0(gInvIls) + '</div></div>' +
    '<div class="sum-card"><div class="sum-card-label">שווי נוכחי</div><div class="sum-card-value">₪' + fmt0(gCurIls) + '</div></div>' +
    '<div class="sum-card"><div class="sum-card-label">רווח / הפסד</div><div class="sum-card-value" style="color:' + pnlColor + '">' + ((gCurIls - gInvIls) >= 0 ? '+' : '') + '₪' + fmt2(gCurIls - gInvIls) + '</div></div>' +
    '<div class="sum-card"><div class="sum-card-label">תשואה כוללת</div><div class="sum-card-value" style="color:' + pnlColor + '">' + (gPct >= 0 ? '+' : '') + gPct.toFixed(2) + '%</div></div>' +
    '</div>' +
    '<div class="summary-bottom">' +
    '<div class="table-wrap summary-table-wrap"><table>' +
    '<thead><tr><th>קטגוריה</th><th>פוזיציות</th><th>הושקע ($)</th><th>הושקע (₪)</th><th>שווי נוכחי (₪)</th><th>תשואה %</th><th>רווח/הפסד (₪)</th></tr></thead>' +
    '<tbody>' + rows +
    '<tr class="tr-total"><td>סה"כ</td><td></td><td></td><td>₪' + fmt0(gInvIls) + '</td>' +
    '<td class="td-value">₪' + fmt2(gCurIls) + '</td>' +
    '<td class="td-pct ' + gCls + '">' + (gPct >= 0 ? '+' : '') + gPct.toFixed(3) + '%</td>' +
    '<td class="td-pct ' + gCls + '">' + ((gCurIls - gInvIls) >= 0 ? '+' : '') + '₪' + fmt2(gCurIls - gInvIls) + '</td>' +
    '</tr></tbody></table></div>' +
    '<div class="pie-wrap"><canvas id="pie-chart"></canvas></div>' +
    '</div>';
}

// --- Pie chart ---

function renderPieChart() {
  var canvas = document.getElementById('pie-chart');
  if (!canvas) return;
  var pieCats = [
    { label: TAB_STOCKS, tabs: [TAB_STOCKS] },
    { label: 'VOO',      tabs: ['VOO'] },
    { label: 'QQQ',      tabs: ['QQQ'] },
    { label: 'CRYPTO',   tabs: ['BTC','ETH'] }
  ];
  var labels = [], values = [], colors = ['#4493f8','#3fb950','#e3b341','#f85149'];

  pieCats.forEach(function(cat, i) {
    var curIls = 0;
    cat.tabs.forEach(function(t) {
      (S.portfolio[t] || []).forEach(function(inv) {
        var pd = S.prices[inv.ticker];
        if (pd && pd.price) curIls += calcCurrentIls(inv, pd.price);
        else curIls += inv.invested_ils;
      });
    });
    if (curIls > 0) {
      labels.push(cat.label);
      values.push(Math.round(curIls));
    }
  });

  var ctx = canvas.getContext('2d');
  S.pieChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors.slice(0, values.length),
        borderColor: '#161b22',
        borderWidth: 3,
        hoverOffset: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: '62%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: '#c9d1d9',
            padding: 16,
            font: { size: 13, weight: '600' }
          }
        },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d',
          borderWidth: 1,
          titleColor: '#8b949e',
          bodyColor: '#c9d1d9',
          callbacks: {
            label: function(ctx) {
              var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
              var pct = total ? (ctx.parsed / total * 100).toFixed(1) : 0;
              return ' ' + ctx.label + ': ' + pct + '% (₪' + ctx.parsed.toLocaleString('en-US') + ')';
            }
          }
        }
      }
    }
  });
}

// --- Chart ---

function onRowClick(ticker) {
  S.chartTicker = ticker;
  document.getElementById('chart-ticker').textContent = ticker;
  var pd = S.prices[ticker];
  if (pd && pd.price) {
    document.getElementById('chart-price').textContent = '$' + fmtPrice(pd.price);
  }
  var chgEl = document.getElementById('chart-change');
  chgEl.textContent = '...';
  chgEl.style.color = 'var(--text-muted)';
  document.getElementById('chart-panel').style.display = 'block';
  loadChart(S.chartRange);
}

var _priceChart = null;
S.chartType = 'line';

function setChartType(type) {
  S.chartType = type;
  document.querySelectorAll('[data-type]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.type === type);
  });
  loadChart(S.chartRange);
}

function closeChart() {
  document.getElementById('chart-panel').style.display = 'none';
  if (_priceChart) { _priceChart.remove(); _priceChart = null; }
  S.chartTicker = null;
}

function loadChart(range) {
  S.chartRange = range;
  document.querySelectorAll('.range-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.range === range);
  });
  if (!S.chartTicker) return;

  var ticker     = S.chartTicker;
  var rangeLabels = { '1d': 'יומי', '7d': 'שבוע', '1mo': 'חודש', '3mo': '3 חודשים', '1y': 'שנה' };
  var chgEl      = document.getElementById('chart-change');

  // Period % change label
  apiFetch('/api/period_change/' + encodeURIComponent(ticker) + '?range=' + range)
    .then(function(d) {
      if (!chgEl) return;
      if (!d || d.pct === null || d.pct === undefined) {
        chgEl.textContent = '—'; chgEl.style.color = 'var(--text-muted)'; return;
      }
      chgEl.textContent = (d.pct >= 0 ? '+' : '') + d.pct.toFixed(2) + '% (' + (rangeLabels[range] || range) + ')';
      chgEl.style.color = d.pct >= 0 ? 'var(--green)' : 'var(--red)';
    })
    .catch(function() {
      if (chgEl) { chgEl.textContent = '—'; chgEl.style.color = 'var(--text-muted)'; }
    });

  // Fetch chart data and draw
  apiFetch('/api/chart/' + encodeURIComponent(ticker) + '?range=' + range)
    .then(function(points) {
      if (!points || !points.length) return;
      var container = document.getElementById('tv-chart');
      if (!container) return;

      if (_priceChart) { _priceChart.remove(); _priceChart = null; }
      container.innerHTML = '';
      container.style.height = '280px';

      var first = points[0].price, last = points[points.length - 1].price;
      var isUp  = last >= first;

      _priceChart = LightweightCharts.createChart(container, {
        width:  container.clientWidth,
        height: 280,
        layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
        grid: {
          vertLines: { color: 'rgba(48,54,61,0.4)' },
          horzLines: { color: 'rgba(48,54,61,0.4)' }
        },
        rightPriceScale: { borderColor: '#30363d' },
        timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
      });

      // Custom tooltip div
      var tooltip = document.createElement('div');
      tooltip.style.cssText = 'position:absolute;display:none;background:#21262d;border:1px solid #30363d;border-radius:5px;padding:5px 8px;font-size:11px;color:#f0f6fc;pointer-events:none;z-index:999;line-height:1.5;';
      container.style.position = 'relative';
      container.appendChild(tooltip);

      if (S.chartType === 'candle') {
        var series = _priceChart.addCandlestickSeries({
          upColor:        '#3fb950',
          downColor:      '#f85149',
          borderUpColor:  '#3fb950',
          borderDownColor:'#f85149',
          wickUpColor:    '#3fb950',
          wickDownColor:  '#f85149'
        });
        var candleData = points.map(function(p) {
          return { time: p.date, open: p.open, high: p.high, low: p.low, close: p.price };
        });
        series.setData(candleData);

        _priceChart.subscribeCrosshairMove(function(param) {
          if (!param || !param.time || !param.seriesData) {
            tooltip.style.display = 'none'; return;
          }
          var d = param.seriesData.get(series);
          if (!d) { tooltip.style.display = 'none'; return; }
          var chg    = d.open ? ((d.close - d.open) / d.open * 100) : 0;
          var chgStr = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
          var chgColor = chg >= 0 ? '#3fb950' : '#f85149';
          tooltip.innerHTML =
            '<span style="color:#8b949e">' + (typeof d.time === 'string' ? d.time : new Date(d.time * 1000).toLocaleDateString()) + '</span><br>' +
            'פתיחה: $' + fmtPrice(d.open) + '<br>' +
            'גבוה: $' + fmtPrice(d.high) + '<br>' +
            'נמוך: $' + fmtPrice(d.low) + '<br>' +
            'סגירה: $' + fmtPrice(d.close) + '<br>' +
            '<span style="color:' + chgColor + ';font-weight:700">שינוי: ' + chgStr + '</span>';
          tooltip.style.display = 'block';
          var x = param.point ? param.point.x : 0;
          var left = x < container.clientWidth / 2 ? (x + 12) : (x - 145);
          tooltip.style.left = left + 'px';
          tooltip.style.top  = '10px';
        });
      } else {
        var color = isUp ? '#3fb950' : '#f85149';
        var series = _priceChart.addLineSeries({
          color: color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true
        });
        series.setData(points.map(function(p) {
          return { time: p.date, value: p.price };
        }));
      }

      _priceChart.timeScale().fitContent();

      window.addEventListener('resize', function() {
        if (_priceChart) _priceChart.applyOptions({ width: container.clientWidth });
      });
    })
    .catch(function(e) { console.error('[chart]', e); });
}

// --- Analyst targets ---

async function fetchTargets() {
  var tickers = [];
  (S.portfolio[TAB_STOCKS] || []).forEach(function(i) {
    if (tickers.indexOf(i.ticker) < 0 && S.targets[i.ticker] === undefined) tickers.push(i.ticker);
  });
  await Promise.allSettled(tickers.map(async function(t) {
    try {
      var d = await apiFetch('/api/target/' + encodeURIComponent(t));
      S.targets[t] = (d && d.target != null) ? d.target : null;
    } catch(e) { S.targets[t] = null; }
  }));
  if (S.activeTab === TAB_STOCKS) renderActiveTab();
}

// --- Form ---

function toggleForm() {
  S.formOpen = !S.formOpen;
  document.getElementById('form-body').style.display = S.formOpen ? 'block' : 'none';
  document.getElementById('form-icon').textContent = S.formOpen ? '▲' : '▼';
}

var _acTimer = null;
var _selTicker = null;
var _selName   = null;

function setupAutocomplete() {
  var input = document.getElementById('f-ticker');
  var list  = document.getElementById('ac-list');

  input.addEventListener('input', function() {
    _selTicker = null; _selName = null;
    clearTimeout(_acTimer);
    var q = input.value.trim();
    if (q.length < 1) { list.style.display = 'none'; return; }
    _acTimer = setTimeout(function() { doAutocomplete(q); }, 280);
  });

  document.addEventListener('click', function(e) {
    if (!e.target.closest('.ac-wrap')) list.style.display = 'none';
  });

  document.getElementById('f-date').addEventListener('change', tryFetchHistoricPrice);
}

async function doAutocomplete(q) {
  var list = document.getElementById('ac-list');
  try {
    var items = await apiFetch('/api/search/' + encodeURIComponent(q));
    if (!items || !items.length) { list.style.display = 'none'; return; }
    list.innerHTML = items.map(function(it) {
      var t = esc(it.ticker), n = esc(it.name || '');
      return '<div class="ac-item" onclick="pickTicker(\'' + t + '\',\'' + n + '\')">' +
        '<span class="ac-ticker">' + it.ticker + '</span>' +
        '<span class="ac-name">' + (it.name || '') + '</span>' +
        '<span class="ac-type">' + (it.type || '') + '</span>' +
        '</div>';
    }).join('');
    list.style.display = 'block';
  } catch(e) { list.style.display = 'none'; }
}

function pickTicker(ticker, name) {
  _selTicker = ticker; _selName = name;
  document.getElementById('f-ticker').value = ticker;
  document.getElementById('f-name-hint').textContent = name;
  document.getElementById('ac-list').style.display = 'none';

  var cat = ticker === 'BTC-USD' || ticker === 'BTC' ? 'BTC'
          : ticker === 'ETH-USD' || ticker === 'ETH' ? 'ETH'
          : ticker === 'VOO' ? 'VOO'
          : ticker === 'QQQ' ? 'QQQ'
          : TAB_STOCKS;
  document.getElementById('f-cat').value = cat;

  tryFetchHistoricPrice();
}

async function tryFetchHistoricPrice() {
  var ticker = _selTicker || document.getElementById('f-ticker').value.trim();
  var date   = document.getElementById('f-date').value;
  if (!ticker || !date) return;
  var prev = document.getElementById('form-preview');
  prev.className = 'form-preview';
  prev.textContent = '⟳ מחפש מחיר ' + ticker + ' בתאריך ' + fmtDate(date) + '...';
  try {
    var d = await apiFetch('/api/history/' + encodeURIComponent(ticker) + '/' + date);
    prev.className = 'form-preview ok';
    prev.innerHTML = '✓ מחיר ' + ticker + ' ב-' + fmtDate(date) + ': <strong>$' + fmtPrice(d.price) + '</strong>' + (d.fallback ? ' (מחיר נוכחי)' : '');
  } catch(e) {
    prev.className = 'form-preview err';
    prev.textContent = '✕ לא נמצא מחיר לתאריך זה';
  }
}

async function addInvestment() {
  var ticker   = (_selTicker || document.getElementById('f-ticker').value).trim().toUpperCase();
  var name     = _selName || ticker;
  var date     = document.getElementById('f-date').value;
  var amount   = parseFloat(document.getElementById('f-amount').value);
  var currency = document.getElementById('f-currency').value;
  var cat      = document.getElementById('f-cat').value;

  if (!ticker || !date || !amount) {
    showPreview('err', '✕ יש למלא: טיקר, תאריך וסכום');
    return;
  }

  var usd, ils;
  if (currency === 'USD') {
    usd = amount;
    ils = parseFloat((amount * S.usdIls).toFixed(2));
  } else {
    ils = amount;
    usd = parseFloat((amount / S.usdIls).toFixed(2));
  }

  var btn = document.querySelector('.btn-add');
  btn.disabled = true;
  btn.textContent = '⟳ מחפש מחיר...';

  var purchasePrice = null;
  try {
    var d = await apiFetch('/api/history/' + encodeURIComponent(ticker) + '/' + date);
    purchasePrice = d.price;
  } catch(e) {}

  if (!purchasePrice) {
    var manual = prompt('לא נמצא מחיר אוטומטי ל-' + ticker + '.\nהזן מחיר קנייה ידנית ($):');
    if (!manual) { btn.disabled = false; btn.textContent = 'הוסף'; return; }
    purchasePrice = parseFloat(manual);
  }

  var inv = {
    id: Date.now().toString(),
    ticker: ticker, name: name,
    invested_usd: usd, invested_ils: ils,
    purchase_date: date, purchase_price: purchasePrice
  };

  if (!S.portfolio[cat]) S.portfolio[cat] = [];
  S.portfolio[cat].push(inv);

  await apiFetch('/api/portfolio', { method: 'POST', json: S.portfolio });

  try {
    var pd = await apiFetch('/api/price/' + encodeURIComponent(ticker));
    if (pd && pd.price) S.prices[ticker] = pd;
  } catch(e) {}

  resetForm();
  btn.disabled = false; btn.textContent = 'הוסף';
  switchTab(cat);
  renderSummaryCards();
  showPreview('ok', '✓ ' + ticker + ' נוסף בהצלחה!');
}

function resetForm() {
  ['f-ticker','f-date','f-amount'].forEach(function(id) {
    document.getElementById(id).value = '';
  });
  document.getElementById('f-name-hint').textContent = '';
  document.getElementById('form-preview').textContent = '';
  _selTicker = null; _selName = null;
}

function showPreview(cls, msg) {
  var el = document.getElementById('form-preview');
  el.className = 'form-preview ' + cls;
  el.textContent = msg;
}

// --- Delete ---

async function delRow(event, cat, id) {
  event.stopPropagation();
  if (!confirm('למחוק השקעה זו?')) return;
  S.portfolio[cat] = (S.portfolio[cat] || []).filter(function(i) { return i.id !== id; });
  await apiFetch('/api/portfolio', { method: 'POST', json: S.portfolio });
  renderActiveTab();
  renderSummaryCards();
}

// --- Flash prices ---

function flashChangedPrices(prev) {
  Object.keys(S.prices).forEach(function(ticker) {
    var data = S.prices[ticker];
    var old  = prev[ticker];
    if (!old || !data || old.price === data.price) return;
    var dir = data.price > old.price ? 'flash-g' : 'flash-r';
    getAllInvestments().forEach(function(inv) {
      if (inv.ticker !== ticker) return;
      var prEl = document.getElementById('pr-' + inv.id);
      if (prEl) {
        prEl.classList.remove('flash-g','flash-r');
        void prEl.offsetWidth;
        prEl.classList.add(dir);
      }
    });
  });
}

// --- Helpers ---

function calcCurrentIls(inv, currentPrice) {
  var qty = inv.invested_usd / inv.purchase_price;
  return qty * currentPrice * S.usdIls;
}

function calcInvestedIls(inv) {
  return inv.invested_usd * S.usdIls;
}

function getAllTickers() {
  var seen = {}, result = [];
  getAllInvestments().forEach(function(inv) {
    if (!seen[inv.ticker]) { seen[inv.ticker] = true; result.push(inv.ticker); }
  });
  return result;
}

function getAllInvestments() {
  var all = [];
  Object.keys(S.portfolio).forEach(function(k) {
    (S.portfolio[k] || []).forEach(function(inv) { all.push(inv); });
  });
  return all;
}

function fmt2(n) { return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmt0(n) { return Math.round(n).toLocaleString('en-US'); }
function fmtPrice(n) {
  if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1)    return Number(n).toFixed(2);
  return Number(n).toFixed(6);
}
function fmtDate(s) {
  if (!s) return '';
  var parts = s.split('-');
  return parts[2] + '/' + parts[1] + '/' + parts[0];
}
function esc(s) { return (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;'); }
function setEl(id, val) { var el = document.getElementById(id); if (el) el.textContent = val; }
