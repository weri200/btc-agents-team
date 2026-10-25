(function(root){
  'use strict';
  const labels={long:'롱 우세',short:'숏 우세',wait:'관망'};
  function demo(symbol='BTCUSDT'){
    const source=[{title:'화면 설명용 가상 시나리오',url:null,asOf:'2026-10-22T00:00:00+09:00'}];
    const horizon=(label,timeframes,rule,verdict,summary,checks,risk,exit)=>({label,timeframes,rule,verdict,summary,agreement:checks.join(' · '),disagreement:risk,condition:exit,explainer:null,
      agents:[{id:'rule',name:'규칙 판정',role:rule,verdict,summary,evidence:checks,counter:risk,invalidation:exit,model:'데모 · 코드 미실행',sources:source}],
      review:{caution:'보통',sources:[{title:'데모 · 가상 거시 지표',url:null,asOf:'2026-10-22T00:00:00+09:00'},{title:'데모 · 가상 기사',url:'https://example.com/demo',asOf:'2026-10-21T09:00:00+09:00'}],checks:[{category:'일정',finding:'가정: 이틀 뒤 미국 물가 지표 발표가 예정되어 있어 변동성이 커질 수 있습니다.',effect:'변동성 확대',news:[]},{category:'뉴스',finding:'가정: ETF 자금 유출 기사가 이어지고 있습니다.',effect:'하락 요인',news:[{publisher:'데모',title:'가상 기사',url:'https://example.com/demo',time:'2026-10-21T09:00:00+09:00'}]}]}});
    return {schemaVersion:1,id:'demo-'+symbol,exchange:'Binance',market:'USDⓈ-M perpetual',symbol,asOf:'2026-10-22T00:00:00+09:00',mode:'demo',promptVersion:'demo-v3',horizons:{
      swing:horizon('4시간 추세 돌파','1일 추세 · 4시간 돌파','1일 추세 + 4시간 20봉 돌파 + 거래량 증가','short','가정: 1일 하락 추세에서 4시간 저점을 거래량과 함께 이탈한 예시입니다.',
        ['1d 추세: 아래 (가정)','4h 20봉 돌파: 저점 이탈 (가정)','4h 거래량: 평소의 1.6배 (충분, 가정)'],'가정: 큰 경제 지표 발표가 가까워 변동성이 커질 수 있는 예시입니다.','4시간 ATR 2배 익절 · 1배 손절 중 먼저 닿는 쪽, 최대 7일 보유.')
    }};
  }
  function validate(r){
    const fail=(m)=>{throw new Error(m)};
    const str=(v,n,max=4000)=>{if(typeof v!=='string'||!v.trim()||v.length>max)fail(n+' 형식을 확인하세요.')};
    const date=(v)=>{str(v,'기준 시각',60);if(!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v)||!Number.isFinite(Date.parse(v)))fail('시각에는 날짜와 시간대가 필요합니다.')};
    const source=(s)=>{if(!s||typeof s!=='object')fail('출처 형식 오류');str(s.title,'출처 제목');date(s.asOf);if(s.url!==null){str(s.url,'출처 URL',2000);try{const u=new URL(s.url);if(!['https:','http:'].includes(u.protocol))fail('허용되지 않은 출처 URL');}catch{fail('출처 URL은 http 또는 https 주소여야 합니다.')}}};
    if(!r||r.schemaVersion!==1)fail('schemaVersion: 1 보고서가 필요합니다.');
    ['id','symbol','promptVersion'].forEach(k=>str(r[k],k,120));
    if(!/^[A-Z0-9]{3,30}$/.test(r.symbol))fail('종목 형식이 올바르지 않습니다.');
    if(r.exchange!=='Binance'||r.market!=='USDⓈ-M perpetual')fail('이 GUI는 Binance USDⓈ-M 무기한 보고서를 지원합니다.');
    if(!['demo','analysis','backtest'].includes(r.mode))fail('mode는 demo, analysis, backtest 중 하나여야 합니다.');date(r.asOf);
    if(!r.horizons?.swing)fail('swing(4시간 추세 돌파) 판정이 필요합니다.');
    // intraday is optional: reports from before rules-v2 carry it, newer ones do not.
    for(const key of ['intraday','swing']){
      const h=r.horizons[key];if(!h)continue;
      ['label','timeframes','summary','agreement','disagreement','condition'].forEach(k=>str(h[k],k));
      if(!Object.hasOwn(labels,h.verdict))fail('판정은 long, short, wait 중 하나여야 합니다.');
      if(!Array.isArray(h.agents)||h.agents.length<1||h.agents.length>12)fail('팀원은 주기별 1~12명이어야 합니다.');
      if(h.review!=null){
        const v=h.review;if(typeof v!=='object'||!['낮음','보통','높음'].includes(v.caution)||!Array.isArray(v.checks)||v.checks.length<1||v.checks.length>12)fail('AI 점검 형식을 확인하세요.');
        for(const c of v.checks){if(!c||typeof c!=='object')fail('AI 점검 항목 형식 오류');['category','finding','effect'].forEach(k=>str(c[k],k));
          if(!Array.isArray(c.news)||c.news.length>12)fail('AI 점검 기사 형식 오류');
          for(const n of c.news){if(!n||typeof n!=='object')fail('AI 점검 기사 형식 오류');str(n.title,'기사 제목');str(n.publisher,'기사 출처',200);date(n.time);if(typeof n.url!=='string'||!/^https?:\/\//.test(n.url))fail('기사 URL은 http 또는 https 주소여야 합니다.')}}
        // What the explainer read; reports before this list carried it in the rule card's sources instead.
        if(v.sources!=null){if(!Array.isArray(v.sources)||v.sources.length>40)fail('AI가 읽은 자료 형식 오류');for(const s of v.sources)source(s)}
      }
      const ids=new Set();for(const a of h.agents){
        if(!a||typeof a!=='object')fail('팀원 형식이 올바르지 않습니다.');
        ['id','name','role','summary','counter','invalidation','model'].forEach(k=>str(a[k],k));
        if(ids.has(a.id))fail('팀원 ID가 중복됩니다.');ids.add(a.id);
        if(!Object.hasOwn(labels,a.verdict))fail('팀원 판정 형식이 올바르지 않습니다.');
        if(!Array.isArray(a.evidence)||a.evidence.length<1||a.evidence.length>12)fail('근거는 1~12개가 필요합니다.');a.evidence.forEach(e=>str(e,'근거'));
        if(!Array.isArray(a.sources)||a.sources.length<1||a.sources.length>12)fail('출처는 1~12개가 필요합니다.');
        for(const s of a.sources)source(s);
      }
    }return r;
  }
  function counts(h){return h.agents.reduce((c,a)=>{c[a.verdict]++;return c},{long:0,short:0,wait:0})}
  const api={demo,validate,counts,labels};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DeskModel=api;
})(typeof globalThis==='undefined'?this:globalThis);
