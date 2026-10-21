(function(root){
  'use strict';
  const labels={long:'롱 우세',short:'숏 우세',wait:'관망'};
  function demo(symbol='BTCUSDT'){
    const agent=(id,name,role,verdict,summary,evidence,counter,invalidation)=>({id,name,role,verdict,summary,evidence,counter,invalidation,model:'데모 · 모델 미실행',sources:[{title:'화면 설명용 가상 시나리오',url:null,asOf:'2026-10-22T00:00:00+09:00'}]});
    return {schemaVersion:1,id:'demo-'+symbol,exchange:'Binance',market:'USDⓈ-M perpetual',symbol,asOf:'2026-10-22T00:00:00+09:00',mode:'demo',promptVersion:'demo-v1',horizons:{
      intraday:{label:'단타',timeframes:'5분 · 15분 · 1시간',verdict:'wait',summary:'단기 반등 의견은 있지만, 추세 확인과 수급 근거가 충분하지 않은 예시입니다.',agreement:'기술적 반등 가능성은 검토하되, 진입 판단에는 추가 데이터가 필요합니다.',disagreement:'기술 분석은 롱 우세, 수급·뉴스·반대 검토는 관망입니다.',condition:'마감된 봉의 돌파와 거래량을 함께 확인하고, 확인되지 않으면 관망을 유지합니다.',agents:[
        agent('technical','기술 분석가','가격 · 추세 · 모멘텀','long','단기 반등 가능성. 추세 전환 확정은 보류합니다.',['가정: 단기 저점이 높아지는 구간입니다.','가정: 중기 추세 확인은 아직 끝나지 않았습니다.'],'횡보장에서 돌파 신호가 실패할 수 있습니다.','반등 구조가 무너지거나 마감 봉이 돌파를 확인하지 못하면 의견을 철회합니다.'),
        agent('derivatives','파생상품 분석가','펀딩 · 미결제약정 · 수급','wait','가격과 미결제약정의 동행 여부를 더 확인해야 합니다.',['가정: 반등이 신규 매수인지 숏 청산인지 구별되지 않습니다.','실제 펀딩비·미결제약정 데이터는 연결되지 않았습니다.'],'가격 상승만 보고 신규 롱 유입으로 해석할 수 없습니다.','시각이 일치하는 펀딩·미결제약정 자료가 없으면 판정을 보류합니다.'),
        agent('macro','뉴스·거시 분석가','이벤트 · 거시 환경','wait','확인된 이벤트 자료가 없어 방향 판단을 보류합니다.',['실제 뉴스 또는 경제 일정은 조회하지 않았습니다.','기사 공개 시각과 원문을 확인한 뒤 분석해야 합니다.'],'오래된 뉴스나 출처 없는 게시물이 판단을 왜곡할 수 있습니다.','신뢰 가능한 새 정보가 확인되면 재분석합니다.'),
        agent('critic','반대 검토자','반론 · 실패 조건','wait','단기 반등에 동의하더라도 진입 근거는 부족합니다.',['분석가 간 의견이 갈리는 예시입니다.','수수료와 슬리피지를 차감한 기대값은 검증되지 않았습니다.'],'신호 확인을 기다리면 일부 가격 움직임을 놓칠 수 있습니다.','데이터 품질과 거래 비용을 검증한 뒤 재평가합니다.')
      ]},
      swing:{label:'스윙',timeframes:'4시간 · 1일',verdict:'short',summary:'상위 시간대의 약세 지속을 가정한 예시입니다. 단타의 반등 가능성과 구분해서 봅니다.',agreement:'상위 추세가 회복되기 전까지 보수적으로 접근한다는 의견입니다.',disagreement:'기술·수급은 숏 우세지만, 뉴스와 반대 검토는 근거 부족으로 관망합니다.',condition:'일봉 추세 회복이 확인되면 숏 우세 판단을 철회하고 다시 분석합니다.',agents:[
        agent('technical','기술 분석가','가격 · 추세 · 모멘텀','short','상위 시간대 하락 구조가 유지되는 상황을 가정합니다.',['가정: 일봉의 고점이 낮아지고 있습니다.','가정: 4시간 봉의 반등이 상위 추세를 바꾸지 못했습니다.'],'단기 반등이 강한 추세 전환으로 이어질 수 있습니다.','일봉 기준 추세 회복이 확인되면 숏 우세를 철회합니다.'),
        agent('derivatives','파생상품 분석가','펀딩 · 미결제약정 · 수급','short','반등 중 롱 쏠림이 발생한 가상의 수급 상황입니다.',['가정: 반등 과정에서 레버리지 롱이 증가했습니다.','실제 수급 수치 없이 만든 화면 설명용 의견입니다.'],'과열 신호만으로 가격 하락을 보장할 수 없습니다.','현물 매수 주도 상승 등 반대 자료가 확인되면 재평가합니다.'),
        agent('macro','뉴스·거시 분석가','이벤트 · 거시 환경','wait','스윙 기간의 거시 이벤트를 확인할 자료가 없습니다.',['금리·유동성 관련 실제 자료는 조회하지 않았습니다.','OpenBB 데이터 공급자 연결이 필요합니다.'],'큰 이벤트가 기술적 추세를 빠르게 바꿀 수 있습니다.','자료가 없거나 오래되면 방향 판정을 보류합니다.'),
        agent('critic','반대 검토자','반론 · 실패 조건','wait','방향 의견과 실제 진입의 유리함은 별도입니다.',['진입 위치·손절·보유 중 펀딩 비용은 미검증입니다.','예시의 다수 의견을 승률로 해석하지 않습니다.'],'과도한 관망은 유효한 추세 기회를 놓칠 수 있습니다.','순차 모의매매와 비용 검증을 통과하면 다시 검토합니다.')
      ]}
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
    if(!['demo','analysis'].includes(r.mode))fail('mode는 demo 또는 analysis여야 합니다.');date(r.asOf);
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
