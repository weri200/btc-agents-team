'use strict';
const {demo,validate,counts,labels}=DeskModel;
const $=s=>document.querySelector(s);
$('#menu-toggle').onclick=()=>{
 const collapsed=!$('#sidebar').hidden;
 $('#sidebar').hidden=collapsed;
 document.body.classList.toggle('sidebar-collapsed',collapsed);
 $('#menu-toggle').setAttribute('aria-expanded',String(!collapsed));
 $('#menu-toggle').setAttribute('aria-label',collapsed?'사이드 메뉴 펼치기':'사이드 메뉴 접기');
};
const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=v=>new Date(v).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' KST';
// One call since rules-v2; the 'swing' key matches the scores of earlier reports.
let report=demo(),horizon='swing',history=[],toastTimer;
try{const saved=JSON.parse(localStorage.getItem('futures-desk-v1')||'[]');if(Array.isArray(saved))history=saved.slice(0,20).filter(r=>{try{validate(r);return true}catch{return false}})}catch{history=[]}// Drop only invalid entries; clearing all would erase them on the next save.
function toast(msg){$('#toast').textContent=msg;$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',5000)}
function saveHistory(){try{localStorage.setItem('futures-desk-v1',JSON.stringify(history));return true}catch{toast('브라우저 저장 공간 부족: 이번 보고서는 현재 화면에서만 볼 수 있습니다.');return false}}
function page(name){document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==name);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.page===name));$('#page-name').textContent={desk:'분석 데스크',history:'판단 이력',system:'연결 및 구성'}[name];if(name==='history')renderHistory();if(name==='system')loadRoles()}
function opinionContext(){
 const mismatch=$('#chart-symbol').value!==report.symbol;
 $('#opinion-context').textContent=`${report.symbol} · ${report.mode==='demo'?'분석 전 · 아직 결과가 없습니다':report.mode==='backtest'?'백테스트 보고서 · 과거 시점 재현':(report.origin==='local-llm'?'로컬 분석 보고서 · 생성 시점의 스냅샷':'가져온 보고서 · 실시간 분석 아님')}${mismatch?' · 차트 종목과 다릅니다.':''}`;
}
$('#chart-symbol').addEventListener('change',opinionContext);
function render(){
 opinionContext();
 const isDemo=report.mode==='demo';$('#notice-title').textContent=isDemo?'아직 분석 결과가 없습니다.':(report.origin==='local-llm'?'코드 규칙 판정 + 로컬 모델 해설 — 판정은 규칙이 냈고, 해설 문장의 정확성은 검증되지 않았습니다.':'가져온 분석 보고서 — 내용의 정확성은 검증되지 않았습니다.');
 $('#notice-text').textContent=isDemo?' 서버를 켠 뒤 처음 열면 실제 수집값으로 로컬 분석을 자동으로 시작하고, 그 뒤에는 서버의 마지막 분석 결과를 불러옵니다.':(report.origin==='local-llm'?' 판정은 서버가 계산한 지표에 코드 규칙을 적용해 냈습니다. 로컬 모델은 그 이유와 규칙 밖의 위험만 설명하며 판정을 바꾸지 않습니다. '+(Array.isArray(report.warnings)?report.warnings.join(' '):''):' 실시간 연결이 아닌 파일의 결과입니다. 데이터 기준 시각과 각 출처를 확인하세요.');
 if(report.mode==='backtest'){const s=report.priceScale;$('#notice-title').textContent='백테스트 보고서 — 과거 시점을 재현한 분석이며 실시간 분석이 아닙니다.';$('#notice-text').textContent=` ${time(report.asOf)} 이전 자료만 썼습니다. `+(s?`이전 버전 보고서라 모델은 날짜 없이 가격을 ×${s} 배율로 받았고, 근거의 가격은 그 값 ÷ ${s}가 실제 가격입니다. `:'코드 규칙만 적용했고 모델은 실행하지 않았습니다. ')+`결과(최종 판정, 실제 가격으로 채점) — ${report.horizons.swing.label} ${labels[report.horizons.swing.verdict]} ${outcomeText(report.horizons.swing.outcome)}. `+(Array.isArray(report.warnings)?report.warnings.join(' '):'')}
 $('#export').disabled=isDemo;  // Nothing real to save before the first result.
 $('#source-badge').textContent=isDemo?'분석 전':report.mode==='backtest'?'백테스트':(report.origin==='local-llm'?'규칙 + 해설':'가져온 보고서');$('#report-time').textContent=isDemo?'':(report.mode==='backtest'?'백테스트 시점 · ':'보고서 기준 · ')+time(report.asOf);
 if(!Array.from($('#symbol').options).some(o=>o.value===report.symbol))$('#symbol').add(new Option(report.symbol,report.symbol));$('#symbol').value=report.symbol;
 $('.coin').textContent=report.symbol.startsWith('BTC')?'₿':report.symbol.startsWith('ETH')?'Ξ':'◈';
 // Demo opinions are made-up text; show nothing until a real analysis exists.
 if(isDemo){
  $('#horizons').innerHTML='<div class="empty horizons-empty"><b>아직 분석 결과가 없습니다.</b>분석이 끝나면 4시간 추세 돌파 판정이 여기에 표시됩니다.</div>';
  $('#agent-count').textContent='00';
  $('#agents').innerHTML='<div class="empty agents-empty"><b>아직 분석 결과가 없습니다.</b>분석이 끝나면 판정 근거가 여기에 표시됩니다.</div>';
  return;
 }
 $('#horizons').innerHTML=Object.entries(report.horizons).filter(([k])=>k===horizon).map(([key,h])=>{const c=counts(h);return `<article class="horizon-card ${key} ${key===horizon?'selected':''}"><div class="horizon-top"><h3>${esc(h.label)}</h3><span>${esc(h.timeframes)} 봉</span></div><div class="horizon-verdict"><strong class="${h.verdict}">${labels[h.verdict]}</strong><span class="tag neutral">${'rule' in h?'코드 규칙 판정':'보고서 종합 판정'}</span>${h.review?`<span class="tag ${CAUTION[h.review.caution]||'neutral'}">AI 주의 ${esc(h.review.caution)}</span>`:''}${h.outcome?`<span class="tag neutral">${esc(outcomeText(h.outcome))}</span>`:''}</div><p>${esc(h.summary)}</p><div class="horizon-bottom"><span>${'rule' in h?esc(h.rule):`롱 ${c.long} · 숏 ${c.short} · 관망 ${c.wait}`}</span><button class="text-btn" data-select="${key}">${'rule' in h?'판정 근거 보기':'팀원 의견 보기'} ↗</button></div></article>`}).join('');
 const h=report.horizons[horizon];$('#agent-count').textContent=String(h.agents.length).padStart(2,'0');
 $('#agents').innerHTML=judgeCard(h)+(h.review?reviewCard(h.review):'')+h.agents.map((a,i)=>a.id==='rule'?ruleCard(a):`<article class="agent"><div class="agent-head"><span class="agent-icon">${['⌁','⇄','◎','◇'][i%4]}</span><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><h3>${esc(a.name)}</h3><div class="role">${esc(a.role)}</div><div class="agent-explanation"><p><span>판단</span>${esc(a.summary)}</p><p><span>근거</span>${esc(a.evidence[0])}</p><p><span>${a.id==='rule'?'청산':'재검토'}</span>${esc(a.invalidation)}</p></div><div class="agent-footer"><span>${esc(a.model)}${a.verification?` · 수치 ${esc(a.verification.matched)}/${esc(a.verification.checked)} 일치`:''}</span><button class="text-btn" data-agent="${i}" aria-label="${esc(a.name)}의 근거 보기">근거 보기 ↗</button></div></article>`).join('');
}
// The explainer's review of what the rule does not read: schedules, news and sentiment. Advisory only; the call stays the code's.
const CAUTION={'낮음':'long','보통':'wait','높음':'short'},EFFECT={'상승 요인':'long','하락 요인':'short','변동성 확대':'wait'};
function reviewCard(r){
 const link=n=>/^https?:\/\//.test(n.url)?`<a class="source-link" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.publisher)} · ${esc(n.title)} ↗</a>`:`${esc(n.publisher)} · ${esc(n.title)}`;
 return `<article class="agent judge-card review-card"><div class="agent-head"><span class="agent-icon">◎</span><span class="tag ${CAUTION[r.caution]||'neutral'}">주의 ${esc(r.caution)}</span></div><h3>AI 정성 점검</h3><div class="role">일정 · 뉴스 · 시장 분위기 · 참고용이며 판정은 바꾸지 않습니다</div><ul class="review-list">${r.checks.map(c=>`<li><div class="review-tags"><span class="tag neutral">${esc(c.category)}</span><span class="tag ${EFFECT[c.effect]||'neutral'}">${esc(c.effect)}</span></div><p>${esc(c.finding)}</p>${c.news.length?`<div class="review-news">${c.news.map(n=>`${link(n)} <span class="muted">· ${esc(time(n.time))}</span>`).join('<br>')}</div>`:''}</li>`).join('')}</ul>${r.sources&&r.sources.length?`<details class="review-sources"><summary>AI가 읽은 자료 ${r.sources.length}개</summary><ul>${r.sources.map(s=>`<li>${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)} <span class="muted">· ${esc(time(s.asOf))}</span></li>`).join('')}</ul></details>`:''}</article>`;
}
// The code's rule in full: every condition with its value, the exit and its prices, the risks outside the rule and the data used.
function ruleCard(a){
 const sources=a.sources.map(s=>`<li>${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)} <span class="muted">· ${esc(time(s.asOf))}</span></li>`).join('');
 return `<article class="agent judge-card rule-card"><div class="agent-head"><span class="agent-icon">⌁</span><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><h3>${esc(a.name)}</h3><div class="role">${esc(a.role)}</div><div class="rule-body"><section><h4>판단</h4><p>${esc(a.summary)}</p><h4>규칙 조건과 값</h4><ol>${a.evidence.map(e=>`<li>${esc(e)}</li>`).join('')}</ol></section><section><h4>청산 기준</h4><p>${esc(a.invalidation)}</p><h4>규칙 밖의 위험</h4><p>${esc(a.counter)}</p></section><section class="rule-sources"><h4>사용한 자료</h4><ul>${sources}</ul></section></div><div class="agent-footer"><span>${esc(a.model)} · ${esc(report.promptVersion)}</span></div></article>`;
}
function verification(a){const v=a.verification;if(!v)return '';const changed=v.failed.length?` · 불일치가 있어 원래 판정(${esc(labels[v.originalVerdict]||v.originalVerdict)})을 관망으로 바꿨습니다.`:'';return `<div class="detail-label">수치 검증</div><div class="detail-copy">인용 ${esc(v.checked)}개 중 ${esc(v.matched)}개가 서버 계산값과 일치${changed}${v.numbersInText?' · 문장에 검증하지 않은 숫자가 있습니다.':''}</div>`}
// The final call leads the section as one card spanning both columns. Reports before rules-v1 came from an LLM judge.
function judgeCard(h){
 const c=counts(h),changed=h.judge&&h.judge.originalVerdict!==h.verdict?` · 판정자 원래 판정 ${esc(labels[h.judge.originalVerdict]||h.judge.originalVerdict)} → 지지하는 팀원이 없어 관망`:'';
 const ruled='rule' in h,model=ruled?(h.explainer?'해설 '+h.explainer.model:'해설 없음'):h.judge?h.judge.model:'보고서 종합';
 return `<article class="agent judge-card"><div class="agent-head"><span class="agent-icon">⚖</span><span class="tag ${h.verdict}">${labels[h.verdict]}</span></div><h3>${ruled?'최종 판정':'종합 판정자'}</h3><div class="role">${ruled?'코드 규칙 · '+esc(h.rule):'팀원 의견 종합 · 최종 판단'}</div><div class="agent-explanation judge-explanation">${(ruled?[['판단',h.summary],['조건',h.agreement],['위험',h.disagreement],['청산',h.condition]]:[['판단',h.summary],['공통',h.agreement],['이견',h.disagreement],['재검토',h.condition]]).map(([k,v])=>`<p><span>${k}</span>${esc(v)}</p>`).join('')}</div><div class="agent-footer"><span>${esc(model)}${ruled?'':` · 롱 ${c.long} · 숏 ${c.short} · 관망 ${c.wait}${changed}`}</span></div></article>`;
}
function detail(index){const a=report.horizons[horizon].agents[index];if(!a)return;$('#detail-content').innerHTML=`<div class="detail-title"><h2>${esc(a.name)}</h2><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><div class="detail-meta">${esc(report.symbol)} · ${esc(report.horizons[horizon].label)} · ${esc(a.model)}<br>${esc(time(report.asOf))} · 프롬프트 ${esc(report.promptVersion)}</div><p class="detail-copy">${esc(a.summary)}</p><div class="detail-label">${a.id==='rule'?'규칙 조건과 값':'판정을 뒷받침하는 근거'}</div><ul class="detail-list">${a.evidence.map(e=>`<li>${esc(e)}</li>`).join('')}</ul>${verification(a)}<div class="detail-label">${a.id==='rule'?'규칙 밖의 위험 (해설 모델)':'반대 근거 · 다른 가능성'}</div><div class="detail-copy">${esc(a.counter)}</div><div class="detail-label">${a.id==='rule'?'청산 기준':'이 판단을 철회하는 조건'}</div><div class="detail-copy">${esc(a.invalidation)}</div><div class="detail-label">사용한 자료</div>${a.sources.map(s=>`<p class="detail-meta">${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)}<br>자료 기준: ${esc(time(s.asOf))}</p>`).join('')}<p class="footnote">표시 내용은 검토 가능한 근거 요약입니다. 모델의 내부 사고 과정이나 검증된 수익 확률을 의미하지 않습니다.</p>`;$('#detail').showModal()}
function download(r){const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`futures-${r.symbol}-${r.mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const ROLE_NAMES={judge:'최종 판정',technical:'기술 분석가',derivatives:'파생상품 분석가',macro:'뉴스·거시 분석가',critic:'반대 검토자'};
const pct=(v,d=0)=>v===null?'—':(v*100).toFixed(d)+'%';
// Scores come from the server journal, so they cover every local run, not only this browser's history.
async function loadScores(){try{const r=await fetch('/api/scores',{cache:'no-store'});if(!r.ok)throw new Error();const data=await r.json();const h=data.holds,p=data.points,b=data.brackets?.swing;const swing=b?`1시간봉으로 4시간 ATR ${b.target}배 익절·${b.stop}배 손절 중 먼저 닿는 쪽(최대 ${h.swing.hours}시간, 넘으면 그때 종가)으로`:`${h.swing.hours}시간 뒤 종가로`;$('#scores-note').textContent=`판정 직전 15분봉 종가에 진입해 ${swing} 채점합니다. 단타 줄은 rules-v2 이전 보고서입니다. 판정 전의 가격 움직임은 결과에 넣지 않습니다. 점수는 적중 +${p.hit}, 빗나감 ${p.miss}, 관망 ${p.wait}점의 평균(−1~+1)이라 관망은 틀린 판정보다 덜 깎입니다. 적중률·평균 수익률은 롱·숏 판정만 셉니다. 수수료는 반영하지 않습니다.`;$('#scores').innerHTML=data.groups.length?`<div class="market-table-wrap"><table class="market-table"><thead><tr><th>구분</th><th>프롬프트</th><th>기간</th><th>역할</th><th>판정</th><th>롱 · 숏 · 관망</th><th>관망 비율</th><th>채점</th><th>점수</th><th>적중률</th><th>평균 수익률</th><th>대기 · 누락</th></tr></thead><tbody>${data.groups.map(g=>`<tr${g.role==='judge'?' class="judge-row"':''}><td>${g.mode==='backtest'?'백테스트':'실시간'}</td><td>${esc(g.promptVersion)}</td><td>${g.horizon==='intraday'?'단타':'스윙'}</td><td>${esc(ROLE_NAMES[g.role]||g.role)}</td><td>${g.calls}</td><td>${g.long} · ${g.short} · ${g.wait}</td><td>${pct(g.waitRate)}</td><td>${g.scored}</td><td>${points(g.score)}</td><td>${pct(g.hitRate)}</td><td>${g.meanReturnPct===null?'—':(g.meanReturnPct>0?'+':'')+g.meanReturnPct.toFixed(2)+'%'}</td><td>${g.pending} · ${g.missing}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><b>아직 저장된 판정이 없습니다.</b>분석이나 백테스트가 끝나면 판정이 서버에 저장되고, 보유 기간이 지나면 채점됩니다.</div>'}catch{$('#scores').innerHTML='<p class="footnote">채점 결과를 불러오지 못했습니다. 서버를 다시 시작했는지 확인하세요.</p>'}}
const OUTCOMES={hit:'적중',miss:'빗나감',wait:'',pending:'채점 대기',missing:'가격 없음'};
const points=v=>typeof v==='number'?(v>0?'+':'')+v.toFixed(2):'—';
function outcomeText(o){if(!o)return '—';return [OUTCOMES[o.status]+(typeof o.return==='number'?` ${o.return>0?'+':''}${(o.return*100).toFixed(2)}%`:''),typeof o.points==='number'?points(o.points)+'점':''].filter(Boolean).join(' · ')}
// Backtests live in the server journal, apart from this browser's report history.
async function loadBacktests(){try{const r=await fetch('/api/backtests',{cache:'no-store'});if(!r.ok)throw new Error();const data=await r.json();$('#backtests-note').textContent=`과거 시점의 자료로 코드 규칙을 다시 적용한 판정입니다. 결과는 최종 판정을 실제 가격으로 채점한 값입니다. 가격 배율은 모델이 판정하던 이전 버전 보고서에만 있습니다. 최근 ${data.reports.length}건 / 전체 ${data.total}건.`;$('#backtests').innerHTML=data.reports.length?`<div class="market-table-wrap"><table class="market-table"><thead><tr><th>시점</th><th>종목</th><th>프롬프트</th><th>판정 · 결과</th><th>가격 배율</th><th>보고서</th></tr></thead><tbody>${data.reports.map(r=>`<tr><td>${esc(time(r.asOf))}</td><td>${esc(r.symbol)}</td><td>${esc(r.promptVersion)}</td>${(h=>h?`<td><span class="tag ${esc(h.verdict)}">${esc(labels[h.verdict]||h.verdict)}</span> ${esc(outcomeText(h))}</td>`:'<td>—</td>')(r.horizons.swing)}<td>${r.priceScale===null?'—':'×'+esc(r.priceScale)}</td><td><button class="text-btn" data-backtest="${esc(r.id)}">보고서 열기 ↗</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><b>아직 백테스트 결과가 없습니다.</b>터미널에서 python backtest.py를 실행하면 시점별 판정과 결과가 여기에 표시됩니다.</div>'}catch{$('#backtests').innerHTML='<p class="footnote">백테스트 결과를 불러오지 못했습니다. 서버를 다시 시작했는지 확인하세요.</p>'}}
async function openBacktest(id){try{const r=await fetch('/api/backtests?id='+encodeURIComponent(id),{cache:'no-store'});const data=await r.json();if(!r.ok)throw new Error(data.error||'불러오지 못했습니다.');report=validate(data);render();page('desk')}catch(e){toast('백테스트 보고서 열기 실패: '+e.message)}}
function renderHistory(){loadScores();loadBacktests();$('#history-list').innerHTML=history.length?history.map((r,i)=>`<article class="history-entry"><div><b>${esc(r.symbol)}</b> <span class="tag neutral">${r.mode==='demo'?'가져온 예시':(r.origin==='local-llm'?'로컬 분석':'가져온 분석')}</span><p>${esc(time(r.asOf))} · ${esc(r.horizons.swing.label)} ${labels[r.horizons.swing.verdict]}</p></div><button class="btn" data-history="${i}">보고서 열기 ↗</button></article>`).join(''):'<div class="empty"><b>아직 가져온 보고서가 없습니다.</b>분석 데스크에서 JSON 보고서를 가져오면 이곳에 기록됩니다.<br>예시 화면을 보는 것만으로 실제 분석 이력이 만들어지지는 않습니다.</div>'}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>page(b.dataset.page));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.select)$('#agents').scrollIntoView({block:'start'});if(b.dataset.agent!==undefined)detail(Number(b.dataset.agent));if(b.dataset.history!==undefined){report=history[Number(b.dataset.history)];render();page('desk')}if(b.dataset.backtest)openBacktest(b.dataset.backtest)});
$('#close-detail').onclick=()=>$('#detail').close();$('#detail').onclick=e=>{if(e.target===$('#detail')){const r=$('#detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#detail').close()}};
$('#export').onclick=()=>download(report);$('#sample').onclick=()=>download(demo());$('#import').onclick=()=>$('#file').click();
$('#file').onchange=async()=>{const f=$('#file').files[0];if(!f)return;try{if(f.size>500000)throw new Error('500KB 이하의 보고서만 가져올 수 있습니다.');const r=validate(JSON.parse(await f.text()));const next=[r,...history.filter(x=>x.id!==r.id)].slice(0,20);report=r;history=next;const stored=saveHistory();render();page('desk');if(stored)toast('보고서를 가져왔습니다. 출처와 기준 시각을 확인하세요.')}catch(e){toast('가져오기 실패: '+e.message)}finally{$('#file').value=''}};
$('#clear-history').onclick=()=>{if(!history.length){toast('지울 이력이 없습니다.');return}if(confirm('이 브라우저에 보관된 보고서 이력을 지울까요? 원본 파일은 유지됩니다.')){history=[];saveHistory();renderHistory()}};
// Status reflects what is actually wired up; "제외" tools were replaced by direct implementations.
const toolStatus={done:['구현됨','long'],next:['다음 단계','wait'],planned:['예정','neutral'],later:['보류','neutral'],dropped:['제외','neutral']};
$('#integrations').innerHTML=[
 ['Binance API','시세·마크 가격·펀딩·미결제약정·마감 캔들 수집. 분석할 때 펀딩·OI·롱숏·테이커 이력도 받습니다.','done'],
 ['LM Studio','로컬 모델 하나가 코드 규칙의 판정 이유와 규칙 밖의 위험(수급·변동성·뉴스)을 설명합니다. 판정은 바꾸지 않습니다.','done'],
 ['판정 채점','보고서를 저장하고 판정 직전 15분봉 종가에 진입해, 1시간봉으로 4시간 ATR 2배 익절·1배 손절 중 먼저 닿는 쪽(최대 7일)으로 판정 적중을 기록합니다. 과거 시점 백테스트도 같은 기준으로 채점합니다.','done'],
 ['OpenBB','해설 모델에게 미국 국채 금리(Fed), VIX·S&P500(CBOE), 나스닥100·주요 경제 일정·코인 관련 뉴스(Nasdaq)를 제공합니다. API 키가 필요 없는 공급자만 씁니다.','done'],
 ['FRED','CPI·PCE 등 발표 수치를 더 받으려면 무료 API 키가 필요합니다. 키를 넣는 기능은 아직 없습니다.','later'],
 ['Binance 과거 데이터','백테스트용으로 data.binance.vision에서 일별 미결제약정·롱숏 비율을 받아 둡니다.','done'],
 ['NautilusTrader','규칙 판정에 수수료·펀딩·슬리피지·체결 가정을 넣어 백테스트할 때 검토합니다.','later'],
 ['CCXT','Binance 직접 호출로 대체했습니다. 여러 거래소 비교나 주문이 필요해지면 다시 검토합니다.','dropped'],
 ['TradingAgents','여러 모델이 토론해 판정하는 팀 구조는 판정이 규칙을 따르지 않아 폐기했습니다. README의 이전 구조를 참고하세요.','dropped']
].map(([n,d,s])=>`<div class="integration ${s}"><b>${n}</b><p>${d}</p><span class="tag ${toolStatus[s][1]}">${toolStatus[s][0]}</span></div>`).join('');
render();

// Simplified provider marks for the progress cards; not official logo files.
const marks={
 microsoft:'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="2" width="9.5" height="9.5" fill="#f25022"/><rect x="12.5" y="2" width="9.5" height="9.5" fill="#7fba00"/><rect x="2" y="12.5" width="9.5" height="9.5" fill="#00a4ef"/><rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#ffb900"/></svg>',
 mistral:'<svg viewBox="0 0 25 25" aria-hidden="true">'+[['#ffd800',[0,4]],['#ffaf00',[0,1,3,4]],['#ff8205',[0,1,2,3,4]],['#fa500f',[0,2,4]],['#e10500',[0,4]]].map(([c,cols],row)=>cols.map(x=>`<rect x="${x*5}" y="${row*5}" width="5" height="5" fill="${c}"/>`).join('')).join('')+'</svg>',
 gemma:'<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="gemma-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4285f4"/><stop offset="1" stop-color="#9b72cb"/></linearGradient></defs><path d="M12 1C12 7 17 12 23 12C17 12 12 17 12 23C12 17 7 12 1 12C7 12 12 7 12 1Z" fill="url(#gemma-g)"/></svg>',
 qwen:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l8.66 5v10L12 22l-8.66-5V7z" fill="none" stroke="#615ced" stroke-width="2.2"/><path d="M12 7.5a4.5 4.5 0 1 0 2.9 7.95L17 17.5" fill="none" stroke="#615ced" stroke-width="2.2" stroke-linecap="round"/></svg>',
 exaone:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10.5" fill="#a50034"/><text x="12" y="15.6" text-anchor="middle" font-size="9.5" font-weight="700" fill="#fff" font-family="Segoe UI,Arial,sans-serif">EX</text></svg>'
};
function markFor(model){const m=String(model).toLowerCase();const key=m.includes('mistral')?'mistral':m.includes('phi')||m.includes('microsoft')?'microsoft':m.includes('gemma')||m.includes('google')?'gemma':m.includes('qwen')?'qwen':m.includes('exaone')?'exaone':null;return key?marks[key]:`<span class="initials">${esc(m.split('/').pop().slice(0,2).toUpperCase())}</span>`}
const seconds=ms=>{const s=Math.max(0,Math.round(ms/1000));return s<60?s+'초':`${Math.floor(s/60)}분${s%60?` ${s%60}초`:''}`};
let progress=null,progressAt=0,progressTimer=null,tickTimer=null,ownRun=false;
function roleProgress(r){
 if(r.status==='done'||r.status==='failed'||r.status==='skipped')return 1;
 if(r.status!=='running')return 0;
 // Estimated from this model's last duration; held below full until the server reports done.
 return Math.min(.95,(r.elapsedMs+Date.now()-progressAt)/Math.max(r.expectedMs,1000));
}
function roleText(r){
 if(r.status==='done')return '완료 · '+seconds(r.durationMs);
 if(r.status==='failed')return '실패 · '+seconds(r.durationMs||0);
 if(r.status==='skipped')return '자료 없음 · 건너뜀';
 if(r.status==='running')return `분석 중 · ${seconds(r.elapsedMs+Date.now()-progressAt)} / 예상 ${seconds(r.expectedMs)}`;
 return '대기';
}
function renderProgress(){
 const box=$('#team-progress');if(!progress||!Array.isArray(progress.roles)){box.hidden=true;return}
 box.hidden=false;
 box.innerHTML=progress.roles.map(r=>`<div class="role-card ${esc(r.status)}" style="--p:${roleProgress(r).toFixed(3)}" role="group" aria-label="${esc(r.name)} ${esc(roleText(r))}${r.reason?' · '+esc(r.reason):''}"><div class="role-inner"><div class="role-ring"><div class="role-logo">${markFor(r.model)}</div></div><div class="role-text"><b>${esc(r.name)}</b><span title="${esc(r.model)}">${esc(r.model)}</span><em>${esc(roleText(r))}</em>${r.reason?`<small title="${esc(r.reason)}">${esc(r.reason)}</small>`:''}</div></div></div>`).join('');
 const elapsed=seconds((progress.elapsedMs||0)+(progress.state==='running'?Date.now()-progressAt:0));
 if(progress.state==='running')$('#llm-status').textContent=`${progress.symbol} · ${progress.stage==='collecting'?'시장·수급 데이터 수집 중':'해설 모델 분석 중'} · 경과 ${elapsed}${ownRun?'':' · 다른 창에서 시작한 분석입니다.'}`;
 else if(!ownRun&&progress.state==='done'&&progress.reportId&&progress.reportId!==report.id)$('#llm-status').innerHTML=`<span>${esc(progress.symbol)} · 마지막 분석 완료 · ${esc(elapsed)}</span><button class="btn" id="llm-load" type="button">결과 불러오기</button>`;
 else if(!ownRun&&progress.state==='failed')$('#llm-status').textContent=`${progress.symbol} · 마지막 분석 실패: ${progress.error||''}`;
}
function tick(){clearTimeout(tickTimer);if(progress&&progress.state==='running'){renderProgress();tickTimer=setTimeout(tick,250)}}
async function pollProgress(){
 clearTimeout(progressTimer);
 try{
  const response=await fetch('/api/progress',{cache:'no-store'});const data=await response.json();
  if(!data||!['idle','running','done','failed'].includes(data.state))return;
  progress=data.state==='idle'?null:data;progressAt=Date.now();renderProgress();tick();
  if(data.state==='running')progressTimer=setTimeout(pollProgress,1000);
 }catch{progressTimer=setTimeout(pollProgress,3000)}
}
// quiet: leave the status line alone and ignore "no report yet", so a running or failed run stays visible.
async function loadLast(message,quiet){
 try{
  const response=await fetch('/api/last-report',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'결과를 불러오지 못했습니다.');
  const next=validate(data);report=next;history=[next,...history.filter(x=>x.id!==next.id)].slice(0,20);saveHistory();render();
  if(!quiet)$('#llm-status').textContent=next.symbol+' · '+message+' · 기준 '+time(next.asOf);
 }catch(err){if(!quiet)$('#llm-status').textContent='결과 불러오기 실패: '+err.message}
}
document.addEventListener('click',e=>{if(e.target.closest&&e.target.closest('#llm-load'))loadLast('다른 창의 분석 결과를 불러왔습니다')});
$('#analyze').onclick=async()=>{
 const button=$('#analyze');if(button.disabled)return;
 const symbol=$('#chart-symbol').value;button.disabled=true;button.textContent='분석 중…';ownRun=true;
 $('#llm-status').textContent=symbol+' · 시장 데이터를 수집해 코드 규칙으로 판정하고, 로컬 모델이 해설을 씁니다.';
 setTimeout(pollProgress,300);
 try{
  const response=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol})});
  const data=await response.json();
  if(response.status===409){ownRun=false;$('#llm-status').textContent=data.error;pollProgress();return}
  if(!response.ok)throw new Error(data.error||'분석 요청 실패');
  const next=validate(data);if(next.symbol!==symbol||next.mode!=='analysis')throw new Error('분석 응답 종목 또는 유형이 다릅니다.');
  report=next;history=[next,...history.filter(x=>x.id!==next.id)].slice(0,20);saveHistory();render();
  $('#llm-status').textContent=symbol+' · 로컬 분석 완료 · 기준 '+time(next.asOf)+' · 보고서는 자동 갱신되지 않습니다.';
 }catch(e){$('#llm-status').textContent='분석 실패: '+e.message+' 기존 보고서를 유지했습니다.';}
 finally{button.disabled=false;button.textContent='분석하기';if(ownRun)await pollProgress();ownRun=false}
};
// Auto-run only on the first visit after the server starts (progress still idle). Later visits load the server's
// last good report, which it keeps even while a newer run is going or after one failed.
async function start(){
 await pollProgress();
 if(!progress)return $('#analyze').onclick();
 return loadLast('서버의 마지막 분석 결과를 불러왔습니다',progress.state!=='done');
}
if(typeof fetch==='function')start();
async function loadRoles(){
 const list=$('#llm-roles');list.innerHTML='<p class="footnote">LM Studio 모델 상태를 확인하는 중입니다.</p>';
 try{
  const response=await fetch('/api/llm',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'상태 확인 실패');
  list.innerHTML=data.roles.map(r=>`<div class="setting-row"><b>${esc(r.name)}</b><span>${esc(r.model)} · ${r.available?'사용 가능':'LM Studio에 없음'}</span></div>`).join('')+data.warnings.map(w=>`<p class="footnote">${esc(w)}</p>`).join('');
 }catch(e){list.innerHTML=`<p class="footnote">모델 상태 확인 실패: ${esc(e.message)}</p>`}
}
