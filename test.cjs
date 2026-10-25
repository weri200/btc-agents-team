const assert=require('node:assert/strict');
const {demo,validate,counts}=require('./dist/model.js');
const clone=()=>JSON.parse(JSON.stringify(demo()));
assert.equal(validate(demo()).symbol,'BTCUSDT');
assert.deepEqual(counts(demo().horizons.swing),{long:0,short:1,wait:0});
for(const mutate of [r=>r.schemaVersion=2,r=>r.asOf='2026-10-22',r=>r.horizons.swing=null,r=>r.horizons.swing.verdict='BUY',r=>r.horizons.swing.agents[0].sources[0].url='javascript:alert(1)',r=>r.horizons.swing.agents.push({...r.horizons.swing.agents[0]}),r=>r.horizons.swing.agents[0].evidence=[],r=>r.symbol='<script>',r=>r.exchange='Other',r=>r.horizons.swing.review.checks[1].news[0].url='javascript:alert(1)',r=>r.horizons.swing.review.caution='매우']){const r=clone();mutate(r);assert.throws(()=>validate(r));}
const r=clone();r.mode='analysis';r.horizons.swing.agents[0].sources[0].url='https://example.com/evidence';assert.equal(validate(r).mode,'analysis');
// Reports from before rules-v2 also carry an intraday call; it is still checked when present.
const plain=clone();delete plain.horizons.swing.review;assert.equal(validate(plain).id,plain.id);  // the review is optional
const older=clone();older.horizons.intraday=clone().horizons.swing;assert.equal(validate(older).id,older.id);older.horizons.intraday.verdict='BUY';assert.throws(()=>validate(older));
console.log('Passed: demo, vote counts, analysis report, and 11 invalid-input cases.');
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
const element=()=>({children:[],style:{setProperty(k,v){this[k]=v}},setAttribute(k,v){this[k]=v},append(...nodes){this.children.push(...nodes)},replaceChildren(...nodes){this.children=nodes}});
const chartNodes={'market-chart':element(),'chart-symbol':{value:'BTCUSDT'},'chart-link':{},'chart-retry':{},'chart-studies':Object.assign(element(),{closest:()=>menu}),'chart-study-count':{},'chart-studies-reset':{}};
let closeMenu,outsideClick;const menu={open:true,contains:n=>n==='inside',classList:{toggle(){}},getBoundingClientRect:()=>({right:500}),addEventListener(_,fn){closeMenu=fn}};
const chartStore={'futures-desk-studies':JSON.stringify(['STD;RSI','bogus']),'futures-desk-study-inputs':JSON.stringify({'STD;RSI':{length:7},'STD;SMA':{length:0},'STD;MACD':{length:5}})};
let themeChanged;
const chartRoot={dataset:{theme:'dark'}};
vm.runInNewContext(require('node:fs').readFileSync(require('node:path').join(__dirname,'dist/chart.js'),'utf8'),{localStorage:{getItem:k=>chartStore[k]??null,setItem:(k,v)=>{chartStore[k]=v}},document:{getElementById:id=>chartNodes[id],createElement:element,documentElement:chartRoot,addEventListener(_,fn){outsideClick=fn},createTextNode:t=>({textContent:t})},MutationObserver:class{constructor(fn){themeChanged=fn}observe(){}}});
const chartConfig=()=>JSON.parse(chartNodes['market-chart'].children[0].children[2].textContent);
assert.equal(chartConfig().symbol,'BINANCE:BTCUSDT.P');
chartNodes['chart-symbol'].value='ETHUSDT';chartNodes['chart-symbol'].onchange();assert.equal(chartConfig().symbol,'BINANCE:ETHUSDT.P');
assert.equal(chartConfig().interval,'240');
chartRoot.dataset.theme='light';themeChanged();assert.equal(chartConfig().theme,'light');
const sameTheme=chartNodes['market-chart'].children[0];themeChanged();assert.equal(chartNodes['market-chart'].children[0],sameTheme);
chartNodes['market-chart'].children[0].children[2].onerror();assert.match(chartNodes['market-chart'].children[0].children[0].textContent,/네트워크/);
chartNodes['chart-retry'].onclick();assert.equal(chartNodes['market-chart'].children.length,1);
// Saved settings switch a study to the legacy id the widget applies inputs under; invalid or unsupported ones are dropped.
assert.deepEqual(chartConfig().studies,[{id:'RSI@tv-basicstudies',inputs:{length:7}}]);assert.equal(chartConfig().hide_top_toolbar,false);
assert.equal(chartNodes['chart-study-count'].textContent,'지표 1');
const macd=chartNodes['chart-studies'].children.find(l=>l.children[0].value==='STD;MACD').children[0];
const beforeClose=chartNodes['market-chart'].children[0];macd.checked=true;macd.onchange();
// The chart is rebuilt only when the menu closes, never per checkbox.
assert.equal(chartNodes['market-chart'].children[0],beforeClose);
outsideClick({target:'inside'});assert.equal(menu.open,true);
outsideClick({target:'outside'});assert.equal(menu.open,false);closeMenu();
assert.deepEqual(chartConfig().studies,[{id:'RSI@tv-basicstudies',inputs:{length:7}},{id:'STD;MACD'}]);assert.equal(chartNodes['market-chart'].style['--study-panes'],2);assert.equal(chartStore['futures-desk-studies'],'["STD;RSI","STD;MACD"]');
const afterMacd=chartNodes['market-chart'].children[0];closeMenu();assert.equal(chartNodes['market-chart'].children[0],afterMacd);
const row=id=>chartNodes['chart-studies'].children.find(l=>l.children[0].value===id);
assert.equal(row('STD;MACD').children.length,2);  // No settings box where the widget ignores inputs.
const [kcLength,kcMult]=row('STD;Keltner_Channels').children[2].children;
kcLength.value='30';kcLength.onchange();kcMult.value='1.5';kcMult.onchange();
assert.deepEqual(JSON.parse(chartStore['futures-desk-study-inputs'])['STD;Keltner_Channels'],{length:30,multiplier:1.5});
kcLength.value='2.5';kcLength.onchange();assert.equal(kcLength.value,30);  // A fractional length is refused and the last value restored.
kcMult.value='';kcMult.onchange();assert.deepEqual(JSON.parse(chartStore['futures-desk-study-inputs'])['STD;Keltner_Channels'],{length:30});
const rsiLength=row('STD;RSI').children[2].children[0];assert.equal(rsiLength.value,7);
rsiLength.value='21';rsiLength.onchange();closeMenu();
assert.deepEqual(chartConfig().studies,[{id:'RSI@tv-basicstudies',inputs:{length:21}},{id:'STD;MACD'}]);
rsiLength.value='';rsiLength.onchange();closeMenu();assert.deepEqual(chartConfig().studies,[{id:'STD;RSI'},{id:'STD;MACD'}]);
chartNodes['chart-studies-reset'].onclick();closeMenu();assert.deepEqual(chartConfig().studies,[]);assert.equal(chartNodes['chart-study-count'].textContent,'지표');
console.log('Passed: chart symbol, default interval, theme sync, error fallback, retry, and saved study settings.');
