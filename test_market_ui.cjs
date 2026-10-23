'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const makeNode = () => ({hidden:false, textContent:'', children:[], events:{},
  append(child){this.children.push(child)}, replaceChildren(...children){this.children=children},
  addEventListener(name, fn){this.events[name]=fn}});
const nodes = Object.fromEntries(['market-status','market-values','market-refresh','chart-symbol',
  'market-price','market-mark','market-funding','market-oi-label','market-oi','market-times','market-candles','desk'].map(id=>[id,makeNode()]));
nodes['chart-symbol'].value='BTCUSDT';
const pending=[], timers=[];
let now=100000;
const payload = symbol => ({kind:'market-data',symbol,baseAsset:symbol.slice(0,-4),cacheAgeMs:0,
  serverTime:1792625571000,collectedAt:'2026-10-22T00:00:00Z',
  ticker:{price:'100',time:1792625571000},mark:{markPrice:'101',lastFundingRate:'-0.0001',time:1792625571000,nextFundingTime:1792627200000},
  openInterest:{openInterest:'20',time:1792625571000},warnings:[],candleSummary:{'5m':{count:200,lastCloseTime:1792625399999,lastClose:'100'}}});
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'dist/market.js'),'utf8'),{
  document:{getElementById:id=>nodes[id],createElement:makeNode,hidden:false},
  performance:{now:()=>now},AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:fn=>timers.push(fn),
  fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))
});
const finish=async(index,data,status=200)=>{
  pending[index].resolve({ok:status===200,headers:{get:()=> 'application/json'},json:async()=>data});
  await new Promise(resolve=>setImmediate(resolve));
};
(async()=>{
  assert(nodes['market-refresh'].disabled);
  await finish(0,payload('BTCUSDT'));
  assert.equal(nodes['market-values'].hidden,false);
  assert.equal(nodes['market-funding'].textContent,'-0.01000%');
  nodes['chart-symbol'].value='ETHUSDT';
  const first=nodes['chart-symbol'].events.change();
  nodes['chart-symbol'].value='SOLUSDT';
  const second=nodes['chart-symbol'].events.change();
  assert(pending[1].options.signal.aborted);
  await finish(2,payload('SOLUSDT'));await second;
  await finish(1,payload('ETHUSDT'));await first;
  assert.match(nodes['market-status'].textContent,/SOLUSDT/);
  now+=91000;timers[1]();
  assert.match(nodes['market-status'].textContent,/90초/);
  const failed=nodes['market-refresh'].onclick();
  await finish(3,{error:'offline'},502);await failed;
  assert(nodes['market-values'].hidden);
  assert.match(nodes['market-status'].textContent,/offline/);
  assert.equal(nodes['market-refresh'].disabled,false);
  console.log('Passed: market UI values, funding units, stale-response race, stale age, and error clearing.');
})().catch(error=>{console.error(error);process.exitCode=1});
