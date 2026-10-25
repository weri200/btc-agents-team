(function(root){
  'use strict';
  const labels={long:'롱 우세',short:'숏 우세',wait:'관망'};
  function demo(symbol='BTCUSDT'){
    const source=[{title:'화면 설명용 가상 시나리오',url:null,asOf:'2026-10-22T00:00:00+09:00'}];
    const horizon=(label,timeframes,rule,verdict,summary,checks,risk,exit)=>({label,timeframes,rule,verdict,summary,agreement:checks.join(' · '),disagreement:risk,condition:exit,explainer:null,
      agents:[{id:'rule',name:'규칙 판정',role:rule,verdict,summary,evidence:checks,counter:risk,invalidation:exit,model:'데모 · 코드 미실행',sources:source}]});
    return {schemaVersion:1,id:'demo-'+symbol,exchange:'Binance',market:'USDⓈ-M perpetual',symbol,asOf:'2026-10-22T00:00:00+09:00',mode:'demo',promptVersion:'demo-v2',horizons:{
      intraday:horizon('단타','5분 · 15분 · 1시간','1시간 추세 + 15분 20봉 돌파 + 거래량 증가','wait','가정: 1시간 추세는 위지만 15분 돌파가 없어 관망인 예시입니다.',
        ['1h 추세: 위 (가정)','15m 20봉 돌파: 없음 (가정)','15m 거래량: 평소의 0.8배 (부족, 가정)'],'가정: 펀딩이 높아 롱 쏠림이 보이는 예시입니다.','진입 뒤 4시간이 지나면 그때 15m 종가로 청산합니다.'),
      swing:horizon('스윙','4시간 · 1일','1일 추세 + 4시간 20봉 돌파 + 거래량 증가','short','가정: 1일 하락 추세에서 4시간 저점을 거래량과 함께 이탈한 예시입니다.',
        ['1d 추세: 아래 (가정)','4h 20봉 돌파: 저점 이탈 (가정)','4h 거래량: 평소의 1.6배 (충분, 가정)'],'가정: 큰 경제 지표 발표가 가까워 변동성이 커질 수 있는 예시입니다.','4시간 ATR 2배 익절 · 1배 손절 중 먼저 닿는 쪽, 최대 7일 보유.')
    }};
  }
  function validate(r){
    const fail=(m)=>{throw new Error(m)};
    const str=(v,n,max=4000)=>{if(typeof v!=='string'||!v.trim()||v.length>max)fail(n+' 형식을 확인하세요.')};
    const date=(v)=>{str(v,'기준 시각',60);if(!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v)||!Number.isFinite(Date.parse(v)))fail('시각에는 날짜와 시간대가 필요합니다.')};
    if(!r||r.schemaVersion!==1)fail('schemaVersion: 1 보고서가 필요합니다.');
    ['id','symbol','promptVersion'].forEach(k=>str(r[k],k,120));
    if(!/^[A-Z0-9]{3,30}$/.test(r.symbol))fail('종목 형식이 올바르지 않습니다.');
    if(r.exchange!=='Binance'||r.market!=='USDⓈ-M perpetual')fail('이 GUI는 Binance USDⓈ-M 무기한 보고서를 지원합니다.');
    if(!['demo','analysis','backtest'].includes(r.mode))fail('mode는 demo, analysis, backtest 중 하나여야 합니다.');date(r.asOf);
    for(const key of ['intraday','swing']){
      const h=r.horizons?.[key];if(!h)fail('단타와 스윙 보고서가 모두 필요합니다.');
      ['label','timeframes','summary','agreement','disagreement','condition'].forEach(k=>str(h[k],k));
      if(!Object.hasOwn(labels,h.verdict))fail('판정은 long, short, wait 중 하나여야 합니다.');
      if(!Array.isArray(h.agents)||h.agents.length<1||h.agents.length>12)fail('팀원은 주기별 1~12명이어야 합니다.');
      const ids=new Set();for(const a of h.agents){
        if(!a||typeof a!=='object')fail('팀원 형식이 올바르지 않습니다.');
        ['id','name','role','summary','counter','invalidation','model'].forEach(k=>str(a[k],k));
        if(ids.has(a.id))fail('팀원 ID가 중복됩니다.');ids.add(a.id);
        if(!Object.hasOwn(labels,a.verdict))fail('팀원 판정 형식이 올바르지 않습니다.');
        if(!Array.isArray(a.evidence)||a.evidence.length<1||a.evidence.length>12)fail('근거는 1~12개가 필요합니다.');a.evidence.forEach(e=>str(e,'근거'));
        if(!Array.isArray(a.sources)||a.sources.length<1||a.sources.length>12)fail('출처는 1~12개가 필요합니다.');
        for(const s of a.sources){if(!s||typeof s!=='object')fail('출처 형식 오류');str(s.title,'출처 제목');date(s.asOf);if(s.url!==null){str(s.url,'출처 URL',2000);try{const u=new URL(s.url);if(!['https:','http:'].includes(u.protocol))fail('허용되지 않은 출처 URL');}catch{fail('출처 URL은 http 또는 https 주소여야 합니다.')}}}
      }
    }return r;
  }
  function counts(h){return h.agents.reduce((c,a)=>{c[a.verdict]++;return c},{long:0,short:0,wait:0})}
  const api={demo,validate,counts,labels};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DeskModel=api;
})(typeof globalThis==='undefined'?this:globalThis);
