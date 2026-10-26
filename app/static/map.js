(function(){
  const search=document.getElementById('searchText'),op=document.getElementById('operatorFilter'),sort=document.getElementById('sortFilter'),list=document.getElementById('parkingList'),count=document.getElementById('visibleCount'),empty=document.getElementById('noResults'),clear=document.getElementById('clearFilters');
  const cards=[...list.querySelectorAll('.parking-card')];
  let map=null;const activeMarkers={};
  const data=window.PARKING_MARKERS||[];
  if(typeof L!=='undefined'){
    map=L.map('mainMap',{zoomControl:false,scrollWheelZoom:false}).setView([26.2158,127.6835],15);
    L.control.zoom({position:'bottomright'}).addTo(map);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',maxZoom:19}).addTo(map);
    const pins=[];
    data.forEach(p=>{if(p.lat==null||p.lng==null)return;
      const marker=L.marker([p.lat,p.lng],{icon:L.divIcon({html:'<div class="map-marker">P</div>',className:'',iconSize:[39,39],iconAnchor:[19,39]})})
        .bindPopup(`<div class="map-popup"><strong>${escapeHtml(p.name)}</strong><br>${p.verified?'入口確認済み':'位置は未確認'}<br><a href="/parking/${Number(p.id)}">詳細を見る →</a></div>`).addTo(map);
      activeMarkers[p.id]=marker;pins.push(marker);
    });
    if(!pins.length){const tip=document.createElement('div');tip.className='map-empty-tip';tip.textContent='入口位置の現地確認後、ピンが追加されます';document.querySelector('.map-column').appendChild(tip)}
  }else{document.getElementById('mainMap').innerHTML='<div style="padding:25px;color:#657a71">地図を読み込めませんでした。以下の駐車場一覧は利用できます。</div>'}
  function escapeHtml(s){return String(s).replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]))}
  function apply(){const q=search.value.trim().toLowerCase(),brand=op.value;
    let visible=cards.filter(c=>(!q||(c.dataset.name+' '+c.dataset.address).includes(q))&&(!brand||c.dataset.operator===brand));
    visible.sort((a,b)=>sort.value==='capacity'?Number(b.dataset.capacity)-Number(a.dataset.capacity):sort.value==='new'?Number(b.dataset.id)-Number(a.dataset.id):a.dataset.name.localeCompare(b.dataset.name,'ja'));
    for(const c of cards)c.hidden=!visible.includes(c);
    const ad=list.querySelector('.ad-block');
    for(const c of visible)list.appendChild(c);
    if(ad){if(visible.length>3)visible[2].after(ad);else list.appendChild(ad)}
    if(map){for(const [id,marker] of Object.entries(activeMarkers)){if(visible.some(c=>c.dataset.id===id))marker.addTo(map);else marker.remove()}}
    count.textContent=visible.length;empty.hidden=visible.length>0;
  }
  [search,op,sort].forEach(el=>el.addEventListener(el===search?'input':'change',apply));
  clear.addEventListener('click',()=>{search.value='';op.value='';sort.value='name';apply()});
  apply();
})();
