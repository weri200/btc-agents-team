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
assert.match(node('#horizons').innerHTML,/종합 의견이 여기에 표시됩니다/);
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
 assert.equal((html.match(/class="agent-explanation"/g)||[]).length,4);
 assert.equal((html.match(/class="agent-explanation judge-explanation"/g)||[]).length,1);
 assert.equal((html.match(/<p><span>/g)||[]).length,16);
 assert(html.startsWith('<article class="agent judge-card">'));
 assert(html.includes(esc(model.demo().horizons[horizon].summary)));
 assert.match(html,/보고서 종합 · 롱 \d · 숏 \d · 관망 \d/);
 assert(html.includes(model.demo().horizons[horizon].agents[0].invalidation));
}
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
 assert.match(node('#notice-title').textContent,/로컬 모델팀/);
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
 assert.match(node('#llm-status').textContent,/ETHUSDT · 역할별 모델 분석 중 · 경과 1분 15초 · 다른 창에서 시작한 분석입니다/);
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
  const group={promptVersion:'v<5>',horizon:'swing',role:'judge',calls:4,long:2,short:1,wait:1,pending:1,missing:0,scored:2,hits:1,waitRate:0.25,hitRate:0.5,meanReturnPct:1.234};
  const scored=vm.createContext({...context,fetch:async()=>({ok:true,json:async()=>({holds:{intraday:{hours:4},swing:{hours:72}},groups:[group,{...group,role:'technical',scored:0,hitRate:null,meanReturnPct:null}]})})});
  vm.runInContext(source,scored);await vm.runInContext('loadScores()',scored);
  const table=node('#scores').innerHTML;
  assert.match(table,/v&lt;5&gt;/);assert.match(table,/종합 판정자/);assert.match(table,/2 · 1 · 1/);assert.match(table,/50%/);assert.match(table,/\+1\.23%/);assert.match(table,/—/);
  assert.match(node('#scores-note').textContent,/72시간/);
  const offline=vm.createContext({...context,fetch:async()=>({ok:false,json:async()=>({})})});
  vm.runInContext(source,offline);await vm.runInContext('loadScores()',offline);
  assert.match(node('#scores').innerHTML,/불러오지 못했습니다/);
  console.log('Passed: verdict scores table, escaping, unscored rows, and server error.');
 }
})().catch(e=>{console.error(e);process.exit(1)});
