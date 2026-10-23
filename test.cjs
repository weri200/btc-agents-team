const assert=require('node:assert/strict');
const {demo,validate,counts}=require('./dist/model.js');
const clone=()=>JSON.parse(JSON.stringify(demo()));
assert.equal(validate(demo()).symbol,'BTCUSDT');
assert.deepEqual(counts(demo().horizons.intraday),{long:1,short:0,wait:3});
assert.deepEqual(counts(demo().horizons.swing),{long:0,short:2,wait:2});
for(const mutate of [r=>r.schemaVersion=2,r=>r.asOf='2026-10-22',r=>r.horizons.swing=null,r=>r.horizons.intraday.verdict='BUY',r=>r.horizons.intraday.agents[0].sources[0].url='javascript:alert(1)',r=>r.horizons.intraday.agents[1].id='technical',r=>r.horizons.swing.agents[0].evidence=[],r=>r.symbol='<script>',r=>r.exchange='Other']){const r=clone();mutate(r);assert.throws(()=>validate(r));}
const r=clone();r.mode='analysis';r.horizons.intraday.agents[0].sources[0].url='https://example.com/evidence';assert.equal(validate(r).mode,'analysis');
console.log('Passed: demo, vote counts, analysis report, and 9 invalid-input cases.');
const vm=require('node:vm');
const themeSource=require('node:fs').readFileSync(require('node:path').join(__dirname,'dist/theme.js'),'utf8');
for(const saved of [null,'dark','light','invalid']){
 const root={dataset:{}},button={setAttribute(name,value){this[name]=value}},events={};
 let stored=saved;
 const system={matches:true,addEventListener(name,fn){this.change=fn}};
 vm.runInNewContext(themeSource,{matchMedia:()=>system,localStorage:{getItem:()=>stored,setItem:(key,value)=>stored=value},document:{documentElement:root,getElementById:()=>button,addEventListener:(name,fn)=>events[name]=fn}});
 assert.equal(root.dataset.theme,saved==='light'?'light':'dark');
 events.DOMContentLoaded();
 assert.equal(button.checked,root.dataset.theme==='dark');
 button.onchange();assert.equal(stored,root.dataset.theme);
 const chosen=root.dataset.theme;system.matches=false;system.change();assert.equal(root.dataset.theme,chosen);
}
assert.doesNotThrow(()=>vm.runInNewContext(themeSource,{matchMedia:()=>({matches:false,addEventListener(){}}),localStorage:{getItem(){throw Error('blocked')}},document:{documentElement:{dataset:{}},getElementById:()=>null,addEventListener(){}}}));
console.log('Passed: theme preference, system default, toggle, persistence, and unavailable storage.');
const element=()=>({children:[],append(...nodes){this.children.push(...nodes)},replaceChildren(...nodes){this.children=nodes}});
const chartNodes={'market-chart':element(),'chart-symbol':{value:'BTCUSDT'},'chart-interval':{value:'15'},'chart-link':{},'chart-retry':{}};
let themeChanged;
const chartRoot={dataset:{theme:'dark'}};
vm.runInNewContext(require('node:fs').readFileSync(require('node:path').join(__dirname,'dist/chart.js'),'utf8'),{document:{getElementById:id=>chartNodes[id],createElement:element,documentElement:chartRoot},MutationObserver:class{constructor(fn){themeChanged=fn}observe(){}}});
const chartConfig=()=>JSON.parse(chartNodes['market-chart'].children[0].children[2].textContent);
assert.equal(chartConfig().symbol,'BINANCE:BTCUSDT.P');
chartNodes['chart-symbol'].value='ETHUSDT';chartNodes['chart-symbol'].onchange();assert.equal(chartConfig().symbol,'BINANCE:ETHUSDT.P');
chartNodes['chart-interval'].value='60';chartNodes['chart-interval'].onchange();assert.equal(chartConfig().interval,'60');
chartRoot.dataset.theme='light';themeChanged();assert.equal(chartConfig().theme,'light');
chartNodes['market-chart'].children[0].children[2].onerror();assert.match(chartNodes['market-chart'].children[0].children[0].textContent,/네트워크/);
chartNodes['chart-retry'].onclick();assert.equal(chartNodes['market-chart'].children.length,1);
console.log('Passed: chart symbol, interval, theme sync, error fallback, and retry.');
