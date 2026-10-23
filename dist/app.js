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
function page(name){document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==name);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.page===name));$('#page-name').textContent={desk:'분석 데스크',history:'판단 이력',system:'연결 및 구성'}[name];if(name==='history')renderHistory();if(name==='system')loadRoles()}
function opinionContext(){
 const mismatch=$('#chart-symbol').value!==report.symbol;
 $('#opinion-context').textContent=`${report.symbol} · ${report.mode==='demo'?'데모 의견 · 실제 수집값에서 생성되지 않음':(report.origin==='local-llm'?'로컬 모델 보고서 · 생성 시점의 스냅샷':'가져온 보고서 · 실시간 분석 아님')}${mismatch?' · 차트 종목과 다릅니다.':''}`;
}
$('#chart-symbol').addEventListener('change',opinionContext);
function render(){
 opinionContext();
 const isDemo=report.mode==='demo';$('#notice-title').textContent=isDemo?'AI 분석은 데모입니다. 실제 수집 데이터와 연결되지 않습니다.':(report.origin==='local-llm'?'로컬 모델팀 분석 — 인용 수치는 서버 지표와 대조했지만 해석의 정확성은 검증되지 않았습니다.':'가져온 분석 보고서 — 내용의 정확성은 검증되지 않았습니다.');
 $('#notice-text').textContent=isDemo?' 예시 판정으로 화면을 살펴보세요. 로컬 모델팀으로 분석 버튼을 누르면 실제 수집값으로 로컬 분석을 실행합니다.':(report.origin==='local-llm'?' 역할마다 지정한 로컬 모델이 판단하며, 인용 수치가 틀린 의견은 관망으로 바꿨습니다. '+(Array.isArray(report.warnings)?report.warnings.join(' '):''):' 실시간 연결이 아닌 파일의 결과입니다. 데이터 기준 시각과 각 출처를 확인하세요.');
 $('#source-badge').textContent=isDemo?'예시 데이터':(report.origin==='local-llm'?'로컬 모델팀':'가져온 보고서');$('#report-time').textContent=(isDemo?'가상 기준 시각 · ':'보고서 기준 · ')+time(report.asOf);
 if(!Array.from($('#symbol').options).some(o=>o.value===report.symbol))$('#symbol').add(new Option(report.symbol,report.symbol));$('#symbol').value=report.symbol;
 $('.coin').textContent=report.symbol.startsWith('BTC')?'₿':report.symbol.startsWith('ETH')?'Ξ':'◈';
 $('#horizons').innerHTML=Object.entries(report.horizons).filter(([k])=>['intraday','swing'].includes(k)).map(([key,h])=>{const c=counts(h);return `<article class="horizon-card ${key} ${key===horizon?'selected':''}"><div class="horizon-top"><h3>${esc(h.label)}</h3><span>${esc(h.timeframes)} 봉</span></div><div class="horizon-verdict"><strong class="${h.verdict}">${labels[h.verdict]}</strong><span class="tag neutral">${isDemo?'예시 종합 판정':'보고서 종합 판정'}</span></div><p>${esc(h.summary)}</p><div class="horizon-bottom"><span>롱 ${c.long} · 숏 ${c.short} · 관망 ${c.wait}</span><button class="text-btn" data-select="${key}">팀원 의견 보기 ↗</button></div></article>`}).join('');
 document.querySelectorAll('[data-horizon]').forEach(b=>{b.classList.toggle('selected',b.dataset.horizon===horizon);b.setAttribute('aria-pressed',b.dataset.horizon===horizon)});
 const h=report.horizons[horizon];$('#agent-count').textContent=String(h.agents.length).padStart(2,'0');
 $('#agents').innerHTML=h.agents.map((a,i)=>`<article class="agent"><div class="agent-head"><span class="agent-icon">${['⌁','⇄','◎','◇'][i%4]}</span><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><h3>${esc(a.name)}</h3><div class="role">${esc(a.role)}</div><div class="agent-explanation"><p><span>판단</span>${esc(a.summary)}</p><p><span>근거</span>${esc(a.evidence[0])}</p><p><span>재검토</span>${esc(a.invalidation)}</p></div><div class="agent-footer"><span>${esc(a.model)}${a.verification?` · 수치 ${esc(a.verification.matched)}/${esc(a.verification.checked)} 일치`:''}</span><button class="text-btn" data-agent="${i}" aria-label="${esc(a.name)}의 근거 보기">근거 보기 ↗</button></div></article>`).join('');
 $('#synthesis').innerHTML=[['공통 의견',h.agreement],['남은 이견',h.disagreement],['재검토 조건',h.condition],...(h.judge?[['종합 판정 모델',h.judge.model]]:[])].map(([k,v])=>`<div class="synthesis-item"><span>${k}</span><p>${esc(v)}</p></div>`).join('');
}
function verification(a){const v=a.verification;if(!v)return '';const changed=v.failed.length?` · 불일치가 있어 원래 판정(${esc(labels[v.originalVerdict]||v.originalVerdict)})을 관망으로 바꿨습니다.`:'';return `<div class="detail-label">수치 검증</div><div class="detail-copy">인용 ${esc(v.checked)}개 중 ${esc(v.matched)}개가 서버 계산값과 일치${changed}${v.numbersInText?' · 문장에 검증하지 않은 숫자가 있습니다.':''}</div>`}
function detail(index){const a=report.horizons[horizon].agents[index];if(!a)return;$('#detail-content').innerHTML=`<div class="detail-title"><h2>${esc(a.name)}</h2><span class="tag ${a.verdict}">${labels[a.verdict]}</span></div><div class="detail-meta">${esc(report.symbol)} · ${esc(report.horizons[horizon].label)} · ${esc(a.model)}<br>${esc(time(report.asOf))} · 프롬프트 ${esc(report.promptVersion)}</div><p class="detail-copy">${esc(a.summary)}</p><div class="detail-label">판정을 뒷받침하는 근거</div><ul class="detail-list">${a.evidence.map(e=>`<li>${esc(e)}</li>`).join('')}</ul>${verification(a)}<div class="detail-label">반대 근거 · 다른 가능성</div><div class="detail-copy">${esc(a.counter)}</div><div class="detail-label">이 판단을 철회하는 조건</div><div class="detail-copy">${esc(a.invalidation)}</div><div class="detail-label">사용한 자료</div>${a.sources.map(s=>`<p class="detail-meta">${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)}<br>자료 기준: ${esc(time(s.asOf))}</p>`).join('')}<p class="footnote">표시 내용은 검토 가능한 근거 요약입니다. 모델의 내부 사고 과정이나 검증된 수익 확률을 의미하지 않습니다.</p>`;$('#detail').showModal()}
function download(r){const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`futures-${r.symbol}-${r.mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function renderHistory(){$('#history-list').innerHTML=history.length?history.map((r,i)=>`<article class="history-entry"><div><b>${esc(r.symbol)}</b> <span class="tag neutral">${r.mode==='demo'?'가져온 예시':(r.origin==='local-llm'?'로컬 분석':'가져온 분석')}</span><p>${esc(time(r.asOf))} · 단타 ${labels[r.horizons.intraday.verdict]} / 스윙 ${labels[r.horizons.swing.verdict]}</p></div><button class="btn" data-history="${i}">보고서 열기 ↗</button></article>`).join(''):'<div class="empty"><b>아직 가져온 보고서가 없습니다.</b>분석 데스크에서 JSON 보고서를 가져오면 이곳에 기록됩니다.<br>예시 화면을 보는 것만으로 실제 분석 이력이 만들어지지는 않습니다.</div>'}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>page(b.dataset.page));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.select||b.dataset.horizon){horizon=b.dataset.select||b.dataset.horizon;render();if(b.dataset.select)$('#agents').scrollIntoView({block:'start'})}if(b.dataset.agent!==undefined)detail(Number(b.dataset.agent));if(b.dataset.history!==undefined){report=history[Number(b.dataset.history)];render();page('desk')}});
$('#symbol').onchange=()=>{report=demo($('#symbol').value);render();toast('보고서의 데모 종목을 변경했습니다. 실제 수집 종목은 차트에서 선택하세요.')};
$('#close-detail').onclick=()=>$('#detail').close();$('#detail').onclick=e=>{if(e.target===$('#detail')){const r=$('#detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#detail').close()}};
$('#export').onclick=()=>download(report);$('#sample').onclick=()=>download(demo());$('#import').onclick=()=>$('#file').click();
$('#file').onchange=async()=>{const f=$('#file').files[0];if(!f)return;try{if(f.size>500000)throw new Error('500KB 이하의 보고서만 가져올 수 있습니다.');const r=validate(JSON.parse(await f.text()));const next=[r,...history.filter(x=>x.id!==r.id)].slice(0,20);report=r;history=next;const stored=saveHistory();render();page('desk');if(stored)toast('보고서를 가져왔습니다. 출처와 기준 시각을 확인하세요.')}catch(e){toast('가져오기 실패: '+e.message)}finally{$('#file').value=''}};
$('#clear-history').onclick=()=>{if(!history.length){toast('지울 이력이 없습니다.');return}if(confirm('이 브라우저에 보관된 보고서 이력을 지울까요? 원본 파일은 유지됩니다.')){history=[];saveHistory();renderHistory()}};
// Status reflects what is actually wired up; "제외" tools were replaced by direct implementations.
const toolStatus={done:['구현됨','long'],next:['다음 단계','wait'],planned:['예정','neutral'],later:['보류','neutral'],dropped:['제외','neutral']};
$('#integrations').innerHTML=[
 ['Binance API','시세·마크 가격·펀딩·미결제약정·마감 캔들 수집. 분석할 때 펀딩·OI·롱숏·테이커 이력도 받습니다.','done'],
 ['LM Studio','역할별 로컬 모델 분석, 인용 수치 검증, 진행 상황 표시.','done'],
 ['판정 채점','보고서를 저장하고 1시간·4시간·1일 뒤 가격으로 판정 적중을 기록합니다. 외부 도구 없이 직접 구현합니다.','next'],
 ['OpenBB','뉴스·거시 분석가에게 미국 국채 금리(Fed), VIX·S&P500(CBOE), 나스닥100·주요 경제 일정·코인 관련 뉴스(Nasdaq)를 제공합니다. API 키가 필요 없는 공급자만 씁니다.','done'],
 ['FRED','CPI·PCE 등 발표 수치를 더 받으려면 무료 API 키가 필요합니다. 키를 넣는 기능은 아직 없습니다.','later'],
 ['Binance 과거 데이터','data.binance.vision에서 과거 캔들·펀딩·OI를 일괄로 받아 채점과 장기 지표에 씁니다.','planned'],
 ['NautilusTrader','규칙 기반 전략이 생기면 수수료·펀딩·슬리피지·체결 가정을 넣어 백테스트합니다.','later'],
 ['CCXT','Binance 직접 호출로 대체했습니다. 여러 거래소 비교나 주문이 필요해지면 다시 검토합니다.','dropped'],
 ['TradingAgents','팀 분석은 직접 구현했습니다. 역할 구성과 토론 방식만 참고합니다.','dropped']
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
 box.innerHTML=progress.roles.map(r=>`<div class="role-card ${esc(r.status)}" style="--p:${roleProgress(r).toFixed(3)}" role="group" aria-label="${esc(r.name)} ${esc(roleText(r))}"><div class="role-inner"><div class="role-ring"><div class="role-logo">${markFor(r.model)}</div></div><div class="role-text"><b>${esc(r.name)}</b><span title="${esc(r.model)}">${esc(r.model)}</span><em>${esc(roleText(r))}</em></div></div></div>`).join('');
 const elapsed=seconds((progress.elapsedMs||0)+(progress.state==='running'?Date.now()-progressAt:0));
 if(progress.state==='running')$('#llm-status').textContent=`${progress.symbol} · ${progress.stage==='collecting'?'시장·수급 데이터 수집 중':'역할별 모델 분석 중'} · 경과 ${elapsed}${ownRun?'':' · 다른 창에서 시작한 분석입니다.'}`;
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
document.addEventListener('click',async e=>{
 if(!e.target.closest||!e.target.closest('#llm-load'))return;
 try{
  const response=await fetch('/api/last-report',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'결과를 불러오지 못했습니다.');
  const next=validate(data);report=next;history=[next,...history.filter(x=>x.id!==next.id)].slice(0,20);saveHistory();render();
  $('#llm-status').textContent=next.symbol+' · 다른 창의 분석 결과를 불러왔습니다 · 기준 '+time(next.asOf);
 }catch(err){$('#llm-status').textContent='결과 불러오기 실패: '+err.message}
});
$('#analyze').onclick=async()=>{
 const button=$('#analyze');if(button.disabled)return;
 const symbol=$('#chart-symbol').value;button.disabled=true;button.textContent='분석 중…';ownRun=true;
 $('#llm-status').textContent=symbol+' · 시장 데이터를 수집하고 역할별 로컬 모델로 차례로 분석합니다. 모델을 바꿔 불러오므로 수 분 걸릴 수 있습니다.';
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
 finally{button.disabled=false;button.textContent='로컬 모델팀으로 분석';if(ownRun)await pollProgress();ownRun=false}
};
if(typeof fetch==='function')pollProgress();
async function loadRoles(){
 const list=$('#llm-roles');list.innerHTML='<p class="footnote">LM Studio 모델 상태를 확인하는 중입니다.</p>';
 try{
  const response=await fetch('/api/llm',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'상태 확인 실패');
  list.innerHTML=data.roles.map(r=>`<div class="setting-row"><b>${esc(r.name)}</b><span>${esc(r.model)} · ${r.available?'사용 가능':'LM Studio에 없음'}</span></div>`).join('')+data.warnings.map(w=>`<p class="footnote">${esc(w)}</p>`).join('');
 }catch(e){list.innerHTML=`<p class="footnote">모델 상태 확인 실패: ${esc(e.message)}</p>`}
}
