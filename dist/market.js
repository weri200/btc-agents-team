'use strict';
(() => {
  const status = document.getElementById('market-status');
  const values = document.getElementById('market-values');
  const button = document.getElementById('market-refresh');
  const symbol = document.getElementById('chart-symbol');
  const format = value => Number(value).toLocaleString('ko-KR', {maximumFractionDigits:8});
  const time = value => new Date(value).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', hour12:false});
  let request, generation = 0, lastSuccess = null;
  const put = (id, value) => { document.getElementById(id).textContent = value; };
  async function collect() {
    request?.abort();
    request = new AbortController();
    const controller = request;
    const current = ++generation;
    const selected = symbol.value;
    const timeout = setTimeout(() => controller.abort(), 90000);
    button.disabled = true;
    values.hidden = true;
    lastSuccess = null;
    status.textContent = `${selected} 수집 중…`;
    try {
      const response = await fetch(`/api/market?symbol=${encodeURIComponent(selected)}`, {signal:controller.signal, cache:'no-store'});
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('로컬 수집 서버가 필요합니다. python server.py로 실행한 주소를 여세요.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '수집 서버 오류');
      if (current !== generation) return;
      if (data.kind !== 'market-data' || data.symbol !== selected) throw new Error('수집 응답 종목 또는 형식 오류');
      put('market-price', format(data.ticker.price));
      put('market-mark', format(data.mark.markPrice));
      put('market-funding', `${(Number(data.mark.lastFundingRate) * 100).toFixed(5)}%`);
      put('market-oi-label', `미결제약정 · ${data.baseAsset}`);
      put('market-oi', format(data.openInterest.openInterest));
      put('market-times', `KST · 거래소 기준 ${time(data.serverTime)} · 체결가 ${time(data.ticker.time)} · 마크/펀딩 ${time(data.mark.time)} · 미결제약정 ${time(data.openInterest.time)} · 다음 펀딩 ${time(data.mark.nextFundingTime)}`);
      const rows = Object.entries(data.candleSummary).map(([interval, bar]) => {
        const tr = document.createElement('tr');
        for (const value of [interval, bar.count, time(bar.lastCloseTime), format(bar.lastClose)]) {
          const td = document.createElement('td'); td.textContent = value; tr.append(td);
        }
        return tr;
      });
      document.getElementById('market-candles').replaceChildren(...rows);
      values.hidden = false;
      // Use elapsed browser time for staleness; the PC clock may differ from the exchange.
      lastSuccess = performance.now() - data.cacheAgeMs;
      status.textContent = `${selected} · 로컬 저장 완료 · 수집 ${time(data.collectedAt)} KST${data.warnings.length ? ' · 주의: ' + data.warnings.join(' ') : ' · 마감 봉 연속성 확인'}`;
    } catch (error) {
      if (current !== generation) return;
      values.hidden = true;
      status.textContent = `${selected} · 수집 실패: ${error.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : error.message} 이전 수치를 최신값으로 표시하지 않습니다.`;
    } finally {
      clearTimeout(timeout);
      if (current === generation) button.disabled = false;
    }
  }
  button.onclick = collect;
  symbol.addEventListener('change', collect);
  setInterval(() => {
    if (!document.hidden && !document.getElementById('desk').hidden && !button.disabled) collect();
  }, 60000);
  setInterval(() => {
    if (lastSuccess !== null && performance.now() - lastSuccess > 90000) {
      status.textContent = `${symbol.value} · 90초 이상 갱신되지 않은 이전 수집값입니다. 지금 수집을 눌러 확인하세요.`;
      lastSuccess = null;
    }
  }, 5000);
  collect();
})();
