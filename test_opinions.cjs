const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const model=require('./dist/model.js');
const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodes=new Map();
function node(selector){
 if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',textContent:'',value:'BTCUSDT',options:[{value:'BTCUSDT'}],addEventListener(name,fn){this[name]=fn},add(option){this.options.push(option)},setAttribute(name,value){this[name]=value}});
 return nodes.get(selector);
}
const context=vm.createContext({DeskModel:model,document:{body:{classList:{toggle(name,value){this[name]=value}}},querySelector:node,querySelectorAll:()=>[],addEventListener(){}},localStorage:{getItem:()=>null},Option:function(text,value){this.value=value},setTimeout,clearTimeout});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'dist/app.js'),'utf8'),context);
vm.runInContext('render()',context);
assert.match(node('#agents').innerHTML,/아직 분석 결과가 없습니다/);
assert.match(node('#horizons').innerHTML,/최종 판정이 여기에 표시됩니다/);
assert(!node('#horizons').innerHTML.includes(model.demo().horizons.intraday.summary));
assert(!node('#agents').innerHTML.includes(model.demo().horizons.intraday.agents[0].summary));
assert.equal(node('#agent-count').textContent,'00');
assert.equal(node('#export').disabled,true);
assert.equal(node('#symbol').onchange,undefined);  // Changing it used to swap a real report for an empty demo.
console.log('Passed: demo report shows no made-up team opinions.');
context.analyzed=Object.assign(model.demo(),{mode:'analysis',origin:'local-llm'});
vm.runInContext('report=analyzed',context);
for(const horizon of ['intraday','swing']){
 vm.runInContext(`horizon='${horizon}';render()`,context);
 const html=node('#agents').innerHTML;
 assert.equal((html.match(/class="agent-explanation"/g)||[]).length,1);
 assert.equal((html.match(/class="agent-explanation judge-explanation"/g)||[]).length,1);
 assert.equal((html.match(/<p><span>/g)||[]).length,7);
 assert(html.startsWith('<article class="agent judge-card">'));
 assert(html.includes(esc(model.demo().horizons[horizon].summary)));
 assert.match(html,/<h3>최종 판정<\/h3><div class="role">코드 규칙 · /);
 assert.match(html,/<span>조건<\/span>.*<span>위험<\/span>.*<span>청산<\/span>/);
 assert.match(html,/해설 없음<\/span>/);
 assert(html.includes(model.demo().horizons[horizon].agents[0].invalidation));
 assert.match(node('#horizons').innerHTML,/코드 규칙 판정/);
}
// Reports from before rules-v1 have no rule field and keep the LLM judge's layout.
context.older=Object.assign(model.demo(),{mode:'analysis',origin:'local-llm'});
vm.runInContext("delete older.horizons.swing.rule;older.horizons.swing.judge={model:'qwen',originalVerdict:'long'};report=older;horizon='swing';render()",context);
assert.match(node('#agents').innerHTML,/<h3>종합 판정자<\/h3>.*<span>공통<\/span>.*qwen · 롱 0 · 숏 1 · 관망 0 · 판정자 원래 판정 롱 우세/);
vm.runInContext("report=analyzed",context);
node('#chart-symbol').value='ETHUSDT';node('#chart-symbol').change();
assert.match(node('#opinion-context').textContent,/차트 종목과 다릅니다/);
const imported=model.demo();imported.mode='analysis';imported.symbol='ETHUSDT';
imported.horizons.swing.agents[0].evidence[0]='<img src=x onerror=alert(1)>';
context.imported=imported;
vm.runInContext('report=validate(imported);render()',context);
assert.match(node('#opinion-context').textContent,/가져온 보고서/);
assert(!node('#opinion-context').textContent.includes('차트 종목과 다릅니다'));
assert(node('#agents').innerHTML.includes('&lt;img'));
assert(!node('#agents').innerHTML.includes('<img'));
console.log('Passed: both horizons, three explanation rows, symbol mismatch, imported report, HTML escaping.');

node('#sidebar').hidden=false;
node('#menu-toggle').onclick();
assert.equal(node('#sidebar').hidden,true);
assert.equal(node('#menu-toggle')['aria-expanded'],'false');
assert.equal(context.document.body.classList['sidebar-collapsed'],true);
node('#menu-toggle').onclick();
assert.equal(node('#sidebar').hidden,false);
assert.equal(node('#menu-toggle')['aria-expanded'],'true');
assert.equal(context.document.body.classList['sidebar-collapsed'],false);
console.log('Passed: sidebar collapse and expansion with accessible state.');
(async()=>{
 node('#chart-symbol').value='BTCUSDT';
 context.localStorage.setItem=()=>{};
 const generated=model.demo('BTCUSDT');generated.mode='analysis';generated.origin='local-llm';generated.warnings=['시각 주의'];
 context.fetch=async()=>({ok:true,json:async()=>generated});
 await node('#analyze').onclick();
 assert.match(node('#llm-status').textContent,/분석 완료/);
 assert.match(node('#notice-title').textContent,/코드 규칙 판정/);
 assert.match(node('#notice-text').textContent,/시각 주의/);
 const previous=vm.runInContext('report',context);
 context.fetch=async()=>({ok:false,json:async()=>({error:'offline'})});
 await node('#analyze').onclick();
 assert.match(node('#llm-status').textContent,/분석 실패/);
 assert.equal(vm.runInContext('report',context),previous);
 assert.equal(node('#analyze').disabled,false);
 console.log('Passed: local analysis display, history, warning, and failure preserves report.');
 const roles=[['technical','기술 분석가','mistralai/mistral-nemo-instruct-2407','done'],['derivatives','파생상품 분석가','microsoft/phi-4','running'],['critic','반대 검토자','google/gemma-4-12b','pending'],['judge','종합 판정자','huihui-qwen3.8-27b-abliterated','pending']]
  .map(([role,name,model,status])=>({role,name,model,status,expectedMs:60000,...(status==='done'?{durationMs:41000}:{}),...(status==='running'?{elapsedMs:30000}:{})}));
 let state={state:'running',stage:'analyzing',symbol:'ETHUSDT',elapsedMs:75000,roles};
 context.fetch=async url=>({ok:true,status:200,json:async()=>url==='/api/progress'?state:generated});
 await vm.runInContext('pollProgress()',context);
 const cards=node('#team-progress').innerHTML;
 assert.equal(node('#team-progress').hidden,false);
 assert.equal((cards.match(/class="role-card /g)||[]).length,4);
 assert.match(cards,/role-card done" style="--p:1\.000"/);
 assert.match(cards,/role-card running" style="--p:0\.5\d\d"/);
 assert.match(cards,/role-card pending" style="--p:0\.000"/);
 assert.match(cards,/분석 중 · 30초 \/ 예상 1분</);
 assert.match(cards,/#f25022/);
 assert.match(node('#llm-status').textContent,/ETHUSDT · 해설 모델 분석 중 · 경과 1분 15초 · 다른 창에서 시작한 분석입니다/);
 state={state:'failed',symbol:'ETHUSDT',error:'x',roles:[{...roles[0],status:'failed',durationMs:1000,reason:'<b>LM Studio</b> 오류'},{...roles[1],status:'skipped',reason:'OpenBB 자료가 없습니다.'},{...roles[2],status:'pending'}]};
 await vm.runInContext('pollProgress()',context);
 const reasons=node('#team-progress').innerHTML;
 assert.match(reasons,/<small title="&lt;b&gt;LM Studio&lt;\/b&gt; 오류">&lt;b&gt;LM Studio&lt;\/b&gt; 오류<\/small>/);
 assert.match(reasons,/<small title="OpenBB 자료가 없습니다\.">/);
 assert.equal((reasons.match(/<small/g)||[]).length,2);
 console.log('Passed: each failed or skipped role card shows its own escaped reason.');
 state={state:'done',symbol:'ETHUSDT',elapsedMs:200000,reportId:'other-report',roles:roles.map(r=>({...r,status:'done',durationMs:40000}))};
 await vm.runInContext('pollProgress()',context);
 assert.match(node('#llm-status').innerHTML,/마지막 분석 완료 · 3분 20초<\/span><button[^>]*>결과 불러오기/);
 assert.equal((node('#team-progress').innerHTML.match(/--p:1\.000/g)||[]).length,4);
 context.fetch=async()=>({ok:false,status:409,json:async()=>({error:'이미 다른 창에서 분석 중입니다. 진행 상황을 표시합니다.'})});
 const before=vm.runInContext('report',context);
 await node('#analyze').onclick();
 assert.equal(vm.runInContext('report',context),before);
 assert.equal(node('#analyze').disabled,false);
 console.log('Passed: progress rings per role, other-window run, result loading offer, and 409 handling.');
 // Runs last: re-running app.js rebinds handlers on the shared fake nodes.
 {
  const stored=JSON.stringify([model.demo('BTCUSDT'),{schemaVersion:2},model.demo('ETHUSDT')]);
  // Startup: idle server -> analyze; finished run -> load its report; running elsewhere -> only show progress.
  const source=fs.readFileSync(require('node:path').join(__dirname,'dist/app.js'),'utf8');
  const open=async(serverState,hasLast=true)=>{
   const calls=[],last=Object.assign(model.demo(),{mode:'analysis',origin:'local-llm',id:'last-run'});
   const page=vm.createContext({...context,localStorage:{getItem:()=>stored,setItem(){}},fetch:async(url,options)=>{
    calls.push(url+' '+((options&&options.method)||'GET'));
    if(url==='/api/progress')return {ok:true,json:async()=>serverState};
    if(url==='/api/last-report')return hasLast?{ok:true,json:async()=>last}:{ok:false,status:404,json:async()=>({error:'아직 완료된 분석이 없습니다.'})};
    return {ok:false,status:409,json:async()=>({error:'busy'})};
   }});
   vm.runInContext(source,page);
   await new Promise(r=>setTimeout(r,20));
   vm.runInContext('clearTimeout(progressTimer);clearTimeout(tickTimer)',page);  // A running state keeps polling.
   return {calls,report:vm.runInContext('report.id',page),status:vm.runInContext("$('#llm-status').textContent",page),exportOff:vm.runInContext("$('#export').disabled",page)};
  };
  let opened=await open({state:'idle'});
  assert(opened.calls.includes('/api/analyze POST'));
  opened=await open({state:'done',symbol:'BTCUSDT',reportId:'last-run',roles:[]});
  assert(!opened.calls.includes('/api/analyze POST'));assert.equal(opened.report,'last-run');
  assert.equal(opened.exportOff,false);
  // The server keeps its last good report while a newer run is going or after one failed; show it, keep the status.
  opened=await open({state:'running',symbol:'BTCUSDT',stage:'analyzing',elapsedMs:1,roles:[]});
  assert(!opened.calls.includes('/api/analyze POST'));assert.equal(opened.report,'last-run');assert.match(opened.status,/분석 중/);
  opened=await open({state:'failed',symbol:'BTCUSDT',error:'LM Studio 꺼짐',roles:[]});
  assert(!opened.calls.includes('/api/analyze POST'));assert.equal(opened.report,'last-run');assert.match(opened.status,/마지막 분석 실패: LM Studio 꺼짐/);
  opened=await open({state:'failed',symbol:'BTCUSDT',error:'LM Studio 꺼짐',roles:[]},false);
  assert.notEqual(opened.report,'last-run');assert.match(opened.status,/마지막 분석 실패/);assert(!/불러오기 실패/.test(opened.status));
  console.log('Passed: only the first visit after server start analyzes; later visits load the last result.');
  const other=vm.createContext({...context,localStorage:{getItem:()=>stored,setItem(){}},fetch:async()=>({ok:false,status:409,json:async()=>({})})});
  vm.runInContext(source,other);
  assert.equal(vm.runInContext('history.map(r=>r.symbol).join()',other),'BTCUSDT,ETHUSDT');
  console.log('Passed: one invalid saved report does not erase the rest of the history.');
  const group={mode:'backtest',promptVersion:'v<5>',horizon:'swing',role:'judge',calls:4,long:2,short:1,wait:1,pending:1,missing:0,scored:2,hits:1,waitRate:0.25,hitRate:0.5,meanReturnPct:1.234,score:0.375};
  const scored=vm.createContext({...context,fetch:async()=>({ok:true,json:async()=>({holds:{intraday:{hours:4},swing:{hours:168}},brackets:{swing:{target:2,stop:1}},points:{hit:1,miss:-1,wait:-0.25},groups:[group,{...group,mode:'analysis',role:'technical',scored:0,hitRate:null,meanReturnPct:null,score:null}]})})});
  vm.runInContext(source,scored);await vm.runInContext('loadScores()',scored);
  const table=node('#scores').innerHTML;
  assert.match(table,/v&lt;5&gt;/);assert.match(table,/백테스트/);assert.match(table,/실시간/);assert.match(table,/최종 판정/);assert.match(table,/2 · 1 · 1/);assert.match(table,/50%/);assert.match(table,/\+1\.23%/);assert.match(table,/—/);
  assert.match(node('#scores-note').textContent,/ATR 2배 익절·1배 손절 중 먼저 닿는 쪽\(최대 168시간/);assert.match(node('#scores-note').textContent,/관망 -0.25점/);assert.match(node('#scores-note').textContent,/판정 직전 15분봉 종가에 진입해 단타는 4시간 뒤 종가, 스윙은 1시간봉으로/);assert.match(table,/\+0\.38/);
  const offline=vm.createContext({...context,fetch:async()=>({ok:false,json:async()=>({})})});
  vm.runInContext(source,offline);await vm.runInContext('loadScores()',offline);
  assert.match(node('#scores').innerHTML,/불러오지 못했습니다/);
  console.log('Passed: verdict scores table, escaping, unscored rows, and server error.');
  const saved=Object.assign(model.demo(),{id:'bt-1',mode:'backtest',priceScale:1.8928});
  saved.horizons.intraday.verdict='long';saved.horizons.swing.verdict='wait';saved.horizons.intraday.outcome={status:'hit',entry:100,exit:101,return:0.01,points:1};saved.horizons.swing.outcome={status:'wait',points:-0.25};
  const listed={total:3,reports:[{id:'bt-1',symbol:'BTCUSDT',asOf:1792800000000,promptVersion:'v<6>',priceScale:1.8928,
    horizons:{intraday:{verdict:'short',status:'miss',entry:100,exit:101,return:-0.0123,points:-1},swing:{verdict:'wait',status:'wait',points:-0.25}}}]};
  const urls=[];
  const back=vm.createContext({...context,fetch:async url=>{urls.push(url);return {ok:true,json:async()=>url.includes('id=')?saved:listed}}});
  vm.runInContext(source,back);await vm.runInContext('loadBacktests()',back);
  const rows=node('#backtests').innerHTML;
  assert.match(rows,/v&lt;6&gt;/);assert.match(rows,/빗나감 -1\.23% · -1\.00점/);assert.match(rows,/<\/span> -0\.25점/);assert.match(rows,/×1\.8928/);assert.match(rows,/data-backtest="bt-1"/);
  assert.match(node('#backtests-note').textContent,/최근 1건 \/ 전체 3건/);
  await vm.runInContext("openBacktest('bt 1')",back);
  assert.equal(urls.at(-1),'/api/backtests?id=bt%201');
  assert.match(node('#notice-title').textContent,/백테스트 보고서/);
  assert.match(node('#notice-text').textContent,/×1\.8928/);assert.match(node('#notice-text').textContent,/단타 롱 우세 적중 \+1\.00% · \+1\.00점 · 스윙 관망 -0\.25점/);
  assert.equal(node('#source-badge').textContent,'백테스트');
  assert.match(node('#horizons').innerHTML,/적중 \+1\.00%/);
  saved.priceScale=null;await vm.runInContext("openBacktest('bt-1')",back);
  assert.match(node('#notice-text').textContent,/코드 규칙만 적용했고 모델은 실행하지 않았습니다/);
  assert.equal(vm.runInContext('history.length',back),0);  // Opening a backtest does not add it to this browser's history.
  const empty=vm.createContext({...context,fetch:async()=>({ok:true,json:async()=>({total:0,reports:[]})})});
  vm.runInContext(source,empty);await vm.runInContext('loadBacktests()',empty);
  assert.match(node('#backtests').innerHTML,/python backtest\.py/);
  console.log('Passed: backtest table, outcomes, opening one backtest report with its scale notice.');
 }
})().catch(e=>{console.error(e);process.exit(1)});
