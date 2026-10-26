(function(){
  'use strict';
  const search=document.getElementById('searchText');
  const op=document.getElementById('operatorFilter');
  const sort=document.getElementById('sortFilter');
  const list=document.getElementById('parkingList');
  const count=document.getElementById('visibleCount');
  const empty=document.getElementById('noResults');
  const clear=document.getElementById('clearFilters');
  const el=document.getElementById('mainMap');
  if(!search || !op || !sort || !list || !el) return;
  const cards=[...list.querySelectorAll('.parking-card')];
  let map=null;
  const markers={};
  const data=window.PARKING_MARKERS||[];
  const esc=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  function fallbackMap(){
    el.innerHTML='<div class="map-fallback"><b>地図の読み込みに失敗しました</b><p>通信環境や広告ブロック等の影響が考えられます。外部地図と駐車場一覧は引き続き使えます。</p><a href="https://www.openstreetmap.org/#map=15/26.2158/127.6840" target="_blank" rel="noopener noreferrer">OpenStreetMapで国際通り周辺を開く ↗</a></div>';
  }
  function drawMap(){
    if(typeof L==='undefined'){ fallbackMap();return; }
    try{
      map=L.map('mainMap',{zoomControl:false,scrollWheelZoom:false}).setView([26.2158,127.6840],15);
      L.control.zoom({position:'bottomright'}).addTo(map);
      const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
        attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom:19
      }).addTo(map);
      const pins=[];
      data.forEach(p=>{
        if(p.lat==null||p.lng==null) return;
        const content=p.price==null?'料金要確認':`参考 ¥${Number(p.price).toLocaleString('ja-JP')}`;
        const marker=L.marker([p.lat,p.lng],{icon:L.divIcon({
          html:`<div class="map-marker ${p.approx?'map-marker-approx':''}">${p.approx?'?':'P'}</div>`,className:'',iconSize:[39,39],iconAnchor:[19,39]
        })}).bindPopup(`<div class="map-popup"><strong>${esc(p.name)}</strong><br><span class="map-warning">${p.approx?'町丁目付近の目安・入口ではありません':'入口位置登録済み'}</span><br>${esc(content)}<br><a href="/parking/${Number(p.id)}">詳細・正確な住所を見る ↗</a></div>`).addTo(map);
        markers[String(p.id)]=marker;pins.push(marker);
      });
      // Bounds stay around Kokusai-dori even for a single data point.
      if(pins.length>1) map.fitBounds(L.featureGroup(pins).getBounds().pad(0.25),{maxZoom:15});
      setTimeout(()=>map&&map.invalidateSize(),250);
      tiles.on('tileerror',()=>{
        // A failed tile server should not erase markers or list results.
        el.classList.add('tile-error');
      });
    }catch(e){
      console.warn('Map initialization failed',e);
      fallbackMap();map=null;
    }
  }
  // CDN failure or script blocker: external map fallback instead of a blank box.
  if(typeof L!=='undefined')drawMap();
  else setTimeout(()=>{if(typeof L!=='undefined')drawMap();else fallbackMap();},1400);
  function apply(){
    const q=search.value.trim().toLowerCase(), brand=op.value;
    const visible=cards.filter(c=>(!q||(c.dataset.name+' '+c.dataset.address).toLowerCase().includes(q))&&(!brand||c.dataset.operator===brand));
    visible.sort((a,b)=>{
      if(sort.value==='price'){
        const av=a.dataset.price===''?Infinity:Number(a.dataset.price),bv=b.dataset.price===''?Infinity:Number(b.dataset.price);
        return av-bv||a.dataset.name.localeCompare(b.dataset.name,'ja');
      }
      if(sort.value==='capacity')return Number(b.dataset.capacity)-Number(a.dataset.capacity);
      if(sort.value==='new')return Number(b.dataset.id)-Number(a.dataset.id);
      return a.dataset.name.localeCompare(b.dataset.name,'ja');
    });
    for(const c of cards)c.hidden=!visible.includes(c);
    const ad=list.querySelector('.ad-block');
    for(const c of visible)list.appendChild(c);
    if(ad){if(visible.length>3)visible[2].after(ad);else list.appendChild(ad);}
    if(map){for(const [id,marker] of Object.entries(markers)){
      if(visible.some(c=>c.dataset.id===id))marker.addTo(map);else marker.remove();
    }}
    count.textContent=String(visible.length);empty.hidden=visible.length>0;
  }
  [search,op,sort].forEach(x=>x.addEventListener(x===search?'input':'change',apply));
  clear.addEventListener('click',()=>{search.value='';op.value='';sort.value=sort.querySelector('option[value="price"]')?'price':'name';apply();});
  const arrive=document.getElementById('arriveTime'),leave=document.getElementById('leaveTime');
  if(arrive&&leave){
    if(!arrive.value && !leave.value){
      const jst=new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Tokyo'}));
      jst.setHours(jst.getHours()+1,0,0,0);
      const fmt=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
      arrive.value=fmt(jst);jst.setHours(jst.getHours()+2);leave.value=fmt(jst);
    }
    arrive.addEventListener('change',()=>{if(leave.value<=arrive.value){const d=new Date(arrive.value);d.setHours(d.getHours()+2);const fmt=x=>`${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}T${String(x.getHours()).padStart(2,'0')}:${String(x.getMinutes()).padStart(2,'0')}`;leave.value=fmt(d);}});
    document.getElementById('compareForm').addEventListener('submit',e=>{if(leave.value<=arrive.value){e.preventDefault();alert('出庫日時は入庫日時より後にしてください');}});
  }
  apply();
})();
