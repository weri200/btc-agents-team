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
let report=demo(),horizon='intraday',history=[],toastTimer;
try{const saved=JSON.parse(localStorage.getItem('futures-desk-v1')||'[]');if(Array.isArray(saved))history=saved.slice(0,20).map(validate)}catch{history=[]}
function toast(msg){$('#toast').textContent=msg;$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',5000)}
function saveHistory(){try{localStorage.setItem('futures-desk-v1',JSON.stringify(history));return true}catch{toast('브라우저 저장 공간 부족: 이번 보고서는 현재 화면에서만 볼 수 있습니다.');return false}}
function page(name){document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==name);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.page===name));$('#page-name').textContent={desk:'분석 데스크',history:'판단 이력',system:'연결 및 구성'}[name];if(name==='history')renderHistory()}
function opinionContext(){
 const mismatch=$('#chart-symbol').value!==report.symbol;
 $('#opinion-context').textContent=`${report.symbol} · ${report.mode==='demo'?'데모 의견 · 실제 수집값에서 생성되지 않음':'가져온 보고서 · 실시간 분석 아님'}${mismatch?' · 차트 종목과 다릅니다.':''}`;
}
$('#chart-symbol').addEventListener('change',opinionContext);
function render(){
 opinionContext();
 const isDemo=report.mode==='demo';$('#notice-title').textContent=isDemo?'AI 분석은 데모입니다. 실제 수집 데이터와 연결되지 않습니다.':'가져온 분석 보고서 — 내용의 정확성은 검증되지 않았습니다.';
 $('#notice-text').textContent=isDemo?' 예시 판정으로 화면을 살펴보세요. GPT·로컬 모델과 네 라이브러리는 아직 연결되지 않았습니다.':' 실시간 연결이 아닌 파일의 결과입니다. 데이터 기준 시각과 각 출처를 확인하세요.';
 $('#source-badge').textContent=isDemo?'예시 데이터':'가져온 보고서';$('#report-time').textContent=(isDemo?'가상 기준 시각 · ':'보고서 기준 · ')+time(report.asOf);
 if(!Array.from($('#symbol').options).some(o=>o.value===report.symbol))$('#symbol').add(new Option(report.symbol,report.symbol));$('#symbol').value=report.symbol;
 $('.coin').textContent=report.symbol.startsWith('BTC')?'₿':report.symbol.startsWith('ETH')?'Ξ':'◈';
 $('#horizons').innerHTML=Object.entries(report.horizons).filter(([k])=>['intraday','swing'].includes(k)).map(([key,h])=>{const c=counts(h);return `<article class="horizon-card ${key} ${key===horizon?'selected':''}"><div class="horizon-top"><h3>${esc(h.label)}</h3><span>${esc(h.timeframes)} 봉</span></div><div class="horizon-verdict"><strong class="${h.verdict}">${labels[h.verdict]}</strong><span class="tag neutral">${isDemo?'예시 종합 판정':'보고서 종합 판정'}</span></div><p>${esc(h.summary)}</p><div class="horizon-bottom"><span>롱 ${c.long} · 숏 ${c.short} · 관망 ${c.wait}</span><button class="text-btn" data-select="${key}">팀원 의견 보기 ↗</button></div></article>`}).join('');
 document.querySelectorAll('[data-horizon]').forEach(b=>{b.classList.toggle('selected',b.dataset.horizon===horizon);b.setAttribute('aria-pressed',b.dataset.horizon===horizon)});
 const h=report.horizons[horizon];$('#agent-count').textContent=String(h.agents.length).padStart(2,'0');
 $('#agents').innerHTML=h.agents.map((a,i)=>`<article class="agent"><div class="agent-head"><span class="agent-icon">${['⌁','⇄','◎','◇'][i%4]}</span><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><h3>${esc(a.name)}</h3><div class="role">${esc(a.role)}</div><div class="agent-explanation"><p><span>판단</span>${esc(a.summary)}</p><p><span>근거</span>${esc(a.evidence[0])}</p><p><span>재검토</span>${esc(a.invalidation)}</p></div><div class="agent-footer"><span>${esc(a.model)}</span><button class="text-btn" data-agent="${i}" aria-label="${esc(a.name)}의 근거 보기">근거 보기 ↗</button></div></article>`).join('');
 $('#synthesis').innerHTML=[['공통 의견',h.agreement],['남은 이견',h.disagreement],['재검토 조건',h.condition]].map(([k,v])=>`<div class="synthesis-item"><span>${k}</span><p>${esc(v)}</p></div>`).join('');
}
function detail(index){const a=report.horizons[horizon].agents[index];if(!a)return;$('#detail-content').innerHTML=`<div class="detail-title"><h2>${esc(a.name)}</h2><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><div class="detail-meta">${esc(report.symbol)} · ${esc(report.horizons[horizon].label)} · ${esc(a.model)}<br>${esc(time(report.asOf))} · 프롬프트 ${esc(report.promptVersion)}</div><p class="detail-copy">${esc(a.summary)}</p><div class="detail-label">판정을 뒷받침하는 근거</div><ul class="detail-list">${a.evidence.map(e=>`<li>${esc(e)}</li>`).join('')}</ul><div class="detail-label">반대 근거 · 다른 가능성</div><div class="detail-copy">${esc(a.counter)}</div><div class="detail-label">이 판단을 철회하는 조건</div><div class="detail-copy">${esc(a.invalidation)}</div><div class="detail-label">사용한 자료</div>${a.sources.map(s=>`<p class="detail-meta">${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)}<br>자료 기준: ${esc(time(s.asOf))}</p>`).join('')}<p class="footnote">표시 내용은 검토 가능한 근거 요약입니다. 모델의 내부 사고 과정이나 검증된 수익 확률을 의미하지 않습니다.</p>`;$('#detail').showModal()}
function download(r){const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`futures-${r.symbol}-${r.mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function renderHistory(){$('#history-list').innerHTML=history.length?history.map((r,i)=>`<article class="history-entry"><div><b>${esc(r.symbol)}</b> <span class="tag neutral">${r.mode==='demo'?'가져온 예시':'가져온 분석'}</span><p>${esc(time(r.asOf))} · 단타 ${labels[r.horizons.intraday.verdict]} / 스윙 ${labels[r.horizons.swing.verdict]}</p></div><button class="btn" data-history="${i}">보고서 열기 ↗</button></article>`).join(''):'<div class="empty"><b>아직 가져온 보고서가 없습니다.</b>분석 데스크에서 JSON 보고서를 가져오면 이곳에 기록됩니다.<br>예시 화면을 보는 것만으로 실제 분석 이력이 만들어지지는 않습니다.</div>'}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>page(b.dataset.page));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.select||b.dataset.horizon){horizon=b.dataset.select||b.dataset.horizon;render();if(b.dataset.select)$('#agents').scrollIntoView({block:'start'})}if(b.dataset.agent!==undefined)detail(Number(b.dataset.agent));if(b.dataset.history!==undefined){report=history[Number(b.dataset.history)];render();page('desk')}});
$('#symbol').onchange=()=>{report=demo($('#symbol').value);render();toast('보고서의 데모 종목을 변경했습니다. 실제 수집 종목은 차트에서 선택하세요.')};
$('#close-detail').onclick=()=>$('#detail').close();$('#detail').onclick=e=>{if(e.target===$('#detail')){const r=$('#detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#detail').close()}};
$('#export').onclick=()=>download(report);$('#sample').onclick=()=>download(demo());$('#import').onclick=()=>$('#file').click();
$('#file').onchange=async()=>{const f=$('#file').files[0];if(!f)return;try{if(f.size>500000)throw new Error('500KB 이하의 보고서만 가져올 수 있습니다.');const r=validate(JSON.parse(await f.text()));const next=[r,...history.filter(x=>x.id!==r.id)].slice(0,20);report=r;history=next;const stored=saveHistory();render();page('desk');if(stored)toast('보고서를 가져왔습니다. 출처와 기준 시각을 확인하세요.')}catch(e){toast('가져오기 실패: '+e.message)}finally{$('#file').value=''}};
$('#clear-history').onclick=()=>{if(!history.length){toast('지울 이력이 없습니다.');return}if(confirm('이 브라우저에 보관된 보고서 이력을 지울까요? 원본 파일은 유지됩니다.')){history=[];saveHistory();renderHistory()}};
$('#integrations').innerHTML=[['Binance API','공개 시세·펀딩비·미결제약정·마감 캔들 수집'],['OpenBB','거시·뉴스 등 배경 자료 수집'],['CCXT','Binance 선물 시세·펀딩·수급 자료 수집'],['TradingAgents','팀원별 분석과 찬반 검토, 종합 보고서 생성'],['NautilusTrader','백테스트와 비용·체결 가정 검증']].map(([n,d])=>`<div class="integration"><b>${n}</b><p>${d}</p><span class="tag neutral">${n==='Binance API'?'구현됨':'미연결'}</span></div>`).join('');
render();
