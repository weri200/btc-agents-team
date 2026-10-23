'use strict';
(() => {
  const host = document.getElementById('market-chart');
  const symbol = document.getElementById('chart-symbol');
  const interval = document.getElementById('chart-interval');
  function renderChart() {
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
      autosize: true, symbol: ticker, interval: interval.value,
      timezone: 'Asia/Seoul', theme: document.documentElement.dataset.theme,
      style: '1', locale: 'kr', allow_symbol_change: false,
      hide_side_toolbar: true, hide_top_toolbar: true,
      hide_volume: false, calendar: false, withdateranges: true,
      support_host: 'https://www.tradingview.com'
    });
    script.onerror = () => { widget.textContent = '차트를 불러오지 못했습니다. 네트워크 연결을 확인하거나 아래 링크를 이용하세요.'; };
    container.append(widget, credit, script);
    host.replaceChildren(container);
  }
  symbol.onchange = interval.onchange = renderChart;
  document.getElementById('chart-retry').onclick = renderChart;
  new MutationObserver(renderChart).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  renderChart();
})();
