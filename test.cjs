const assert=require('node:assert/strict');
const {demo,validate,counts}=require('./dist/model.js');
const clone=()=>JSON.parse(JSON.stringify(demo()));
assert.equal(validate(demo()).symbol,'BTCUSDT');
assert.deepEqual(counts(demo().horizons.intraday),{long:1,short:0,wait:3});
assert.deepEqual(counts(demo().horizons.swing),{long:0,short:2,wait:2});
for(const mutate of [r=>r.schemaVersion=2,r=>r.asOf='2026-10-22',r=>r.horizons.swing=null,r=>r.horizons.intraday.verdict='BUY',r=>r.horizons.intraday.agents[0].sources[0].url='javascript:alert(1)',r=>r.horizons.intraday.agents[1].id='technical',r=>r.horizons.swing.agents[0].evidence=[],r=>r.symbol='<script>',r=>r.exchange='Other']){const r=clone();mutate(r);assert.throws(()=>validate(r));}
const r=clone();r.mode='analysis';r.horizons.intraday.agents[0].sources[0].url='https://example.com/evidence';assert.equal(validate(r).mode,'analysis');
console.log('Passed: demo, vote counts, analysis report, and 9 invalid-input cases.');
