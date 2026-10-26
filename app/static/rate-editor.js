(function(){
const $=id=>document.getElementById(id);
let conf=structuredClone(window.INITIAL_CONFIG||{});conf.periods=conf.periods||[];conf.caps=conf.caps||[];
const escapeAttr=s=>String(s??'').replace(/[&"<>']/g,a=>({'&':'&amp;','"':'&quot;','<':'&lt;','>':'&gt;',"'":'&#39;'}[a]));
function readMain(){conf.weekday={minutes:Number($('weekdayMinutes').value),yen:Number($('weekdayYen').value)};conf.weekend={minutes:Number($('weekendMinutes').value),yen:Number($('weekendYen').value)};conf.verified_for_calculation=$('verifiedToggle').checked;}
function field(label,type,value,data,extra=''){return `<label>${label}<input type="${type}" ${extra} data-prop="${data}" value="${escapeAttr(value)}"></label>`}
function render(){
  $('weekdayMinutes').value=conf.weekday?.minutes||30;$('weekdayYen').value=conf.weekday?.yen??200;
  $('weekendMinutes').value=conf.weekend?.minutes||30;$('weekendYen').value=conf.weekend?.yen??200;
  $('verifiedToggle').checked=!!conf.verified_for_calculation;
  $('periods').innerHTML=conf.periods.map((p,i)=>`<div class="rule-card" data-p-index="${i}"><div class="rule-title"><span>時間帯料金 ${i+1}</span><button type="button" class="delete-rule" data-delete-period="${i}">削除</button></div><div class="rule-fields">
  ${field('開始時刻','time',p.start||'20:00','start')}${field('終了時刻','time',p.end||'08:00','end')}${field('課金単位（分）','number',p.minutes||60,'minutes','min="1"')}${field('料金（円）','number',p.yen??200,'yen','min="0"')}${field('対象曜日 0(月)〜6(日)','text',(p.weekdays||[0,1,2,3,4,5,6]).join(','),'weekdays','class="wide"')}</div></div>`).join('');
  $('caps').innerHTML=conf.caps.map((p,i)=>`<div class="rule-card" data-c-index="${i}"><div class="rule-title"><span>最大料金ルール ${i+1}</span><button type="button" class="delete-rule" data-delete-cap="${i}">削除</button></div><div class="rule-fields">
  <label class="wide">種別<select data-prop="type"><option value="duration" ${p.type==='duration'?'selected':''}>入庫後◯時間まで</option><option value="daypart" ${p.type==='daypart'?'selected':''}>時間帯（例：20時まで）</option><option value="calendar_day" ${p.type==='calendar_day'?'selected':''}>当日24時まで</option></select></label>
  ${field('最大料金（円）','number',p.yen??800,'yen','min="0"')}
  ${p.type==='duration'?field('入庫後の時間数','number',p.hours||24,'hours','min="1" max="168"'):''}
  ${p.type==='daypart'?field('開始時刻','time',p.start||'08:00','start')+field('終了時刻','time',p.end||'20:00','end'):''}
  ${field('対象曜日 0(月)〜6(日)','text',(p.weekdays||[0,1,2,3,4,5,6]).join(','),'weekdays','class="wide"')}
  <label class="toggle-row wide"><input type="checkbox" data-prop="repeat" ${p.repeat!==false?'checked':''}><span>最大料金を繰り返し適用</span></label></div></div>`).join('');
  $('advancedJson').value=JSON.stringify(conf,null,2);
  sync();
}
function parseDays(v){const days=String(v).split(',').map(s=>Number(s.trim())).filter(x=>Number.isInteger(x)&&x>=0&&x<=6);return [...new Set(days)]}
function pullRule(target){const pidx=target.closest('[data-p-index]'),cidx=target.closest('[data-c-index]');
 if(!pidx&&!cidx)return;const obj=pidx?conf.periods[Number(pidx.dataset.pIndex)]:conf.caps[Number(cidx.dataset.cIndex)];const prop=target.dataset.prop;if(!prop)return;
 if(prop==='repeat')obj[prop]=target.checked;else if(prop==='weekdays')obj[prop]=parseDays(target.value);else if(['minutes','yen','hours'].includes(prop))obj[prop]=Number(target.value);else obj[prop]=target.value;
 if(prop==='type'){render();return}sync();
}
function sync(){readMain();$('rateConfig').value=JSON.stringify(conf);$('advancedJson').value=JSON.stringify(conf,null,2)}
$('addPeriod').addEventListener('click',()=>{conf.periods.push({start:'20:00',end:'08:00',minutes:60,yen:100,weekdays:[0,1,2,3,4,5,6]});render()});
$('addCap').addEventListener('click',()=>{conf.caps.push({type:'duration',hours:24,yen:900,weekdays:[0,1,2,3,4,5,6],repeat:true});render()});
$('periods').addEventListener('input',e=>pullRule(e.target));$('caps').addEventListener('input',e=>pullRule(e.target));
$('periods').addEventListener('change',e=>pullRule(e.target));$('caps').addEventListener('change',e=>pullRule(e.target));
for(const root of ['periods','caps'])$(root).addEventListener('click',e=>{const pi=e.target.dataset.deletePeriod,ci=e.target.dataset.deleteCap;if(pi!==undefined){conf.periods.splice(Number(pi),1);render()}if(ci!==undefined){conf.caps.splice(Number(ci),1);render()}});
['weekdayMinutes','weekdayYen','weekendMinutes','weekendYen','verifiedToggle'].forEach(id=>$(id).addEventListener('input',sync));
$('applyAdvanced').addEventListener('click',()=>{try{const next=JSON.parse($('advancedJson').value);if(typeof next!=='object'||!next)throw Error('オブジェクトにしてください');conf=next;conf.periods=conf.periods||[];conf.caps=conf.caps||[];render();alert('詳細設定を読み込みました')}catch(e){alert('JSONが正しくありません: '+e.message)}});
$('previewPrice').addEventListener('click',async()=>{
 try{sync();const a=$('previewStart').value,b=$('previewEnd').value;if(!a||!b)throw Error('入出庫の日時を入力してください');
 const body=new FormData();body.append('csrf',window.CSRF);body.append('rate_config',JSON.stringify(conf));body.append('arrive',a);body.append('leave',b);
 const response=await fetch('/api/admin/rate-preview',{method:'POST',body,credentials:'same-origin'});if(!response.ok)throw Error('計算APIに接続できません');const result=await response.json();
 if(result.amount==null){$('previewOutput').textContent='料金要確認：'+result.reason}else{$('previewOutput').innerHTML=`<strong>概算 ¥${result.amount.toLocaleString('ja-JP')}</strong><br>${result.breakdown.map(x=>escapeAttr(x.label)+'：'+x.yen.toLocaleString('ja-JP')+'円').join('<br>')}<p>※参考料金です。現地表示を確認してください。</p>`}
 }catch(e){$('previewOutput').textContent=e.message}
});
$('parkingEditor').addEventListener('submit',e=>{sync();if(!confirm('入力内容を保存しますか？'))e.preventDefault()});
render();
})();
