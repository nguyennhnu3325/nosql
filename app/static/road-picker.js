/* Select existing Junctions; preview only, no changes to saved ROAD geometry. */
window.createRoadPicker=function({host,network,source,target,length,initial=null,readOnly=false,isBusy=()=>false}){
 const valid=j=>j.latitude!=null&&j.longitude!=null&&Number.isFinite(Number(j.latitude))&&Number.isFinite(Number(j.longitude))&&Math.abs(Number(j.latitude))<=90&&Math.abs(Number(j.longitude))<=180;
 const junctions=new Map(network.junctions.map(j=>[String(j.junctionId),j]));
 const names=new Map();
 for(const road of network.roads){const name=String(road.properties?.name||'').trim();if(!name)continue;for(const id of [road.source,road.target]){const key=String(id);if(!names.has(key))names.set(key,new Set());names.get(key).add(name);}}
 const describe=id=>{const j=junctions.get(id);return !j?'Chưa chọn':(names.has(id)?[...names.get(id)].slice(0,2).join(' / ')+' · ':'')+'Junction '+id+' ('+j.latitude+', '+j.longitude+')';};
 for(const select of [source,target])for(const option of select.options)if(option.value)option.textContent=describe(option.value);
 const controls=document.createElement('div');controls.className='road-picker-controls';
 const start=document.createElement('button'),end=document.createElement('button'),fit=document.createElement('button');
 for(const button of [start,end,fit]){button.type='button';button.className='secondary';}
 start.textContent='Chọn điểm đầu A';end.textContent='Chọn điểm cuối B';fit.textContent='Xem hai đầu';controls.append(start,end,fit);host.append(controls);
 const status=document.createElement('p');status.className='hint';status.setAttribute('role','status');status.setAttribute('aria-live','polite');host.append(status);
 const summary=document.createElement('p');summary.className='hint';host.append(summary);
 const canvas=document.createElement('div');canvas.className='road-picker-map';canvas.setAttribute('aria-label','Chọn Junction đầu và cuối trên bản đồ');host.append(canvas);
 const note=document.createElement('p');note.className='hint';note.textContent='Bấm vào nút giao trên bản đồ để chọn. Đường nối A–B chỉ minh họa hai đầu, chưa thể hiện hình dáng thực tế của ROAD.';host.append(note);
 let active=source.value?'target':'source',map=null,selected=null,disposed=false;
 function refresh(){
  if(length&&!readOnly){
   const a=junctions.get(source.value),b=junctions.get(target.value);
   if(initial&&String(initial.source)===source.value&&String(initial.target)===target.value)length.value=initial.lengthM??'';
   else if(a&&b&&valid(a)&&valid(b)&&source.value!==target.value){
    const rad=value=>Number(value)*Math.PI/180,dlat=rad(b.latitude)-rad(a.latitude),dlon=rad(b.longitude)-rad(a.longitude);
    const h=Math.sin(dlat/2)**2+Math.cos(rad(a.latitude))*Math.cos(rad(b.latitude))*Math.sin(dlon/2)**2;
    length.value=(6371000*2*Math.asin(Math.sqrt(Math.min(1,h)))).toFixed(2);
   }else length.value='';
   length.setCustomValidity(length.value===''?'Chọn hai Junction có tọa độ hợp lệ để tính chiều dài.':'');
  }
  summary.textContent='A: '+describe(source.value)+' → B: '+describe(target.value);
  status.textContent=readOnly?'Vị trí hai đầu đoạn đường.':active==='source'?'Đang chọn điểm đầu A.':'Đang chọn điểm cuối B.';
  start.setAttribute('aria-pressed',String(active==='source'));end.setAttribute('aria-pressed',String(active==='target'));start.disabled=end.disabled=readOnly||isBusy();
  if(!map)return;
  selected.clearLayers();const coords=[];
  for(const [label,select,color]of [['A',source,'#17866b'],['B',target,'#8b6cb8']]){const j=junctions.get(select.value);if(!j||!valid(j))continue;const point=[Number(j.latitude),Number(j.longitude)];coords.push(point);const text=document.createElement('span');text.textContent=label+' · '+describe(select.value);L.circleMarker(point,{radius:10,color,fillColor:color,fillOpacity:1,weight:3}).bindTooltip(text,{permanent:true,direction:'top'}).addTo(selected);}
  if(coords.length===2)L.polyline(coords,{color:'#d58a29',weight:4,dashArray:'8 6',interactive:false}).addTo(selected);
 }
 start.onclick=()=>{active='source';refresh();};end.onclick=()=>{active='target';refresh();};
 function focus(){if(!map)return;const coords=[source.value,target.value].map(id=>junctions.get(id)).filter(j=>j&&valid(j)).map(j=>[Number(j.latitude),Number(j.longitude)]);if(coords.length)map.fitBounds(coords,{padding:[35,35],maxZoom:17});}
 fit.onclick=focus;
 if(typeof L==='undefined'){canvas.textContent='Không tải được bản đồ. Bạn vẫn có thể chọn Junction bằng danh sách phía trên.';start.hidden=end.hidden=fit.hidden=true;refresh();return {refresh,destroy(){disposed=true;}};}
 map=L.map(canvas,{preferCanvas:true}).setView([10.8065,106.6288],14);selected=L.featureGroup().addTo(map);attachMapBackground(map);
 const points=[];
 for(const [id,j]of junctions){if(!valid(j))continue;const point=[Number(j.latitude),Number(j.longitude)];points.push(point);const text=document.createElement('span');text.textContent=describe(id);L.circleMarker(point,{radius:5,color:'#526d80',fillOpacity:.7,weight:1}).bindTooltip(text).addTo(map).on('click',()=>{
  if(readOnly||isBusy()||disposed)return;
  const input=active==='source'?source:target,other=active==='source'?target:source;
  if(other.value===id){status.textContent='Điểm đầu và điểm cuối phải là hai Junction khác nhau.';return;}
  input.value=id;input.dispatchEvent(new Event('change',{bubbles:true}));
  if(active==='source')active='target';refresh();
 });}
 selected.bringToFront();refresh();if(source.value||target.value)focus();else if(points.length)map.fitBounds(points,{padding:[20,20],maxZoom:16});
 const frame=requestAnimationFrame(()=>{if(!disposed)map.invalidateSize();});
 return {refresh,destroy(){disposed=true;cancelAnimationFrame(frame);map.remove();}};
};
