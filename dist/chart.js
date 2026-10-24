'use strict';
(() => {
  const host = document.getElementById('market-chart');
  const symbol = document.getElementById('chart-symbol');
  const studyBox = document.getElementById('chart-studies');
  const studyCount = document.getElementById('chart-study-count');
  // TradingView built-in studies offered as presets. More can be added from the chart's own indicator menu,
  // but only these are remembered between visits.
  const STUDIES = [
    ['STD;SMA', '이동평균 (SMA)'], ['STD;EMA', '지수이동평균 (EMA)'], ['STD;Bollinger_Bands', '볼린저 밴드'],
    ['STD;VWAP', 'VWAP'], ['STD;RSI', 'RSI'], ['STD;MACD', 'MACD'], ['STD;Stochastic_RSI', '스토캐스틱 RSI'],
    ['STD;Average_True_Range', 'ATR'], ['STD;Ichimoku%1Cloud', '일목균형표'],
    ['STD;Supertrend', '슈퍼트렌드'], ['STD;PSAR', '파라볼릭 SAR'], ['STD;Donchian_Channels', '돈치안 채널'],
    ['STD;Keltner_Channels', '켈트너 채널'], ['STD;Stochastic', '스토캐스틱'], ['STD;DMI', 'DMI (ADX 포함)'],
    ['STD;CCI', 'CCI'], ['STD;Money_Flow', 'MFI'], ['STD;On_Balance_Volume', 'OBV'], ['STD;Willams_R', '윌리엄스 %R'], // TradingView's own spelling
  ];
  // Oscillators open their own pane under the price; the chart grows so the price pane keeps its height.
  const PANE_STUDIES = new Set(['STD;RSI', 'STD;MACD', 'STD;Stochastic_RSI', 'STD;Average_True_Range',
    'STD;Stochastic', 'STD;DMI', 'STD;CCI', 'STD;Money_Flow', 'STD;On_Balance_Volume', 'STD;Willams_R']);
  const KEY = 'futures-desk-studies';
  let selected, renderedTheme, renderedStudies;
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    // Earlier versions saved Ichimoku under a wrong id that TradingView ignores; keep the user's pick.
    selected = Array.isArray(saved) ? saved.map(id => id === 'STD;Ichimoku_Cloud' ? 'STD;Ichimoku%1Cloud' : id).filter(id => STUDIES.some(([s]) => s === id)) : [];
  } catch { selected = []; }

  function showCount() {
    studyCount.textContent = selected.length ? `지표 ${selected.length}` : '지표';
  }

  function renderStudies() {
    studyBox.replaceChildren(...STUDIES.map(([id, name]) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = id;
      input.checked = selected.includes(id);
      input.onchange = () => {
        selected = STUDIES.map(([s]) => s).filter(s => s === id ? input.checked : selected.includes(s));
        try { localStorage.setItem(KEY, JSON.stringify(selected)); } catch {}
        showCount();
      };
      label.append(input, document.createTextNode(name));
      return label;
    }));
    showCount();
  }

  function renderChart() {
    renderedTheme = document.documentElement.dataset.theme;
    renderedStudies = selected.join();
    host.style.setProperty('--study-panes', selected.filter(id => PANE_STUDIES.has(id)).length);
    const ticker = `BINANCE:${symbol.value}.P`;
    const container = document.createElement('div');
    container.className = 'tradingview-widget-container';
    const widget = document.createElement('div');
    widget.className = 'tradingview-widget-container__widget';
    const credit = document.createElement('div');
    credit.className = 'tradingview-widget-copyright';
    const link = document.createElement('a');
    link.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(ticker)}`;
    link.textContent = `${symbol.value} chart by TradingView`;
    link.target = '_blank';
    link.rel = 'noopener nofollow';
    credit.append(link);
    document.getElementById('chart-link').href = link.href;
    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.async = true;
    script.textContent = JSON.stringify({
      autosize: true, symbol: ticker, // Opens on 4h; the chart's own toolbar changes the interval.
      interval: '240',
      timezone: 'Asia/Seoul', theme: renderedTheme,
      style: '1', locale: 'kr', allow_symbol_change: false,
      // The top toolbar carries TradingView's own indicator menu for anything not in the presets.
      hide_side_toolbar: true, hide_top_toolbar: false, save_image: false,
      hide_volume: false, calendar: false, withdateranges: true,
      studies: selected,
      support_host: 'https://www.tradingview.com'
    });
    script.onerror = () => { widget.textContent = '차트를 불러오지 못했습니다. 네트워크 연결을 확인하거나 아래 링크를 이용하세요.'; };
    container.append(widget, credit, script);
    host.replaceChildren(container);
  }

  // Open the menu toward whichever side has room, since the button moves as the controls wrap.
  const menuHost = studyBox.closest('details');
  menuHost.addEventListener('toggle', () => {
    // Rebuild once on close: rebuilding per checkbox removed the iframe mid-load, the same fault as the theme bug.
    if (!menuHost.open) return selected.join() !== renderedStudies && renderChart();
    // Prefer ending at the button's right edge (stays over the chart); flip only when that runs off the left.
    menuHost.classList.toggle('align-left', menuHost.getBoundingClientRect().right - 260 < 16);
  });
  // A click outside closes the menu, so a change applies without reopening it.
  document.addEventListener('click', e => { if (menuHost.open && !menuHost.contains(e.target)) menuHost.open = false; });
  symbol.onchange = renderChart;
  document.getElementById('chart-retry').onclick = renderChart;
  document.getElementById('chart-studies-reset').onclick = () => {
    selected = [];
    try { localStorage.setItem(KEY, '[]'); } catch {}
    renderStudies();
  };
  // theme.js re-applies the same theme on DOMContentLoaded; rebuilding then removed the first iframe mid-load,
  // leaving TradingView bound to a dead frame and the chart mis-sized. Rebuild only on a real change.
  new MutationObserver(() => {
    if (document.documentElement.dataset.theme !== renderedTheme) renderChart();
  }).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  renderStudies();
  renderChart();
})();
