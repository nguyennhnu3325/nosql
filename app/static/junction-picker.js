/* New Junction coordinates are selected freely, independently of existing nodes. */
window.createJunctionPicker=function({host,network,latitude,longitude,entity='junction',readOnly=false,isBusy=()=>false}){
 const isPlace=entity==='place',title=isPlace?'địa điểm':'Junction';
 const status=document.createElement('p');status.className='hint';status.setAttribute('role','status');status.setAttribute('aria-live','polite');host.append(status);
 const canvas=document.createElement('div');canvas.className='road-picker-map';canvas.setAttribute('aria-label','Chọn vị trí '+title+' trên bản đồ');host.append(canvas);
 const note=document.createElement('p');note.className='hint';note.textContent=isPlace?'Bấm trên bản đồ để đặt địa điểm, kéo điểm đánh dấu để chỉnh vị trí hoặc nhập tọa độ phía trên. Các chấm nhỏ là Junction hiện có. Khi lưu, địa điểm được tự động kết nối với Junction gần nhất.':'Bấm trên bản đồ để đặt Junction mới, kéo điểm đánh dấu để chỉnh vị trí hoặc nhập tọa độ phía trên. Các chấm nhỏ là Junction hiện có. Junction mới cần được nối bằng ROAD để tham gia tìm đường.';host.append(note);
 const valid=(lat,lng)=>lat!==''&&lng!==''&&Number.isFinite(Number(lat))&&Number.isFinite(Number(lng))&&Math.abs(Number(lat))<=90&&Math.abs(Number(lng))<=180;
 let map=null,marker=null,disposed=false;
 function refresh(){
  const lat=latitude.value,lng=longitude.value;
  if(!valid(lat,lng)){status.textContent='Chọn một vị trí trên bản đồ hoặc nhập đầy đủ tọa độ hợp lệ.';if(marker){marker.remove();marker=null;}return;}
  status.textContent='Vị trí '+title+': '+lat+', '+lng;
  if(!map)return;
  if(!marker){marker=L.marker([Number(lat),Number(lng)],{draggable:!readOnly,autoPan:true}).addTo(map);marker.on('dragend',()=>{if(readOnly||isBusy()){refresh();return;}const point=marker.getLatLng();choose(point.lat,point.lng);});}
  else marker.setLatLng([Number(lat),Number(lng)]);
 }
 function choose(lat,lng){
  if(disposed||readOnly||isBusy())return;
  latitude.value=Number(lat).toFixed(7);longitude.value=Number(lng).toFixed(7);
  for(const input of [latitude,longitude]){input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
  refresh();
 }
 function fromInputs(){refresh();if(map&&valid(latitude.value,longitude.value))map.panTo([Number(latitude.value),Number(longitude.value)]);}
 latitude.addEventListener('input',fromInputs);longitude.addEventListener('input',fromInputs);
 if(typeof L==='undefined'){canvas.textContent='Không tải được bản đồ. Bạn vẫn có thể nhập tọa độ phía trên.';refresh();return {destroy(){disposed=true;latitude.removeEventListener('input',fromInputs);longitude.removeEventListener('input',fromInputs);}};}
 map=L.map(canvas,{preferCanvas:true,worldCopyJump:true}).setView([10.8065,106.6288],14);attachMapBackground(map);
 const points=[];
 for(const j of network?.junctions??[]){if(j.latitude==null||j.longitude==null||!valid(j.latitude,j.longitude))continue;const point=[Number(j.latitude),Number(j.longitude)];points.push(point);const label=document.createElement('span');label.textContent='Junction '+j.junctionId;L.circleMarker(point,{radius:4,color:'#526d80',fillOpacity:.6,weight:1,interactive:false}).bindTooltip(label).addTo(map);}
 map.on('click',event=>{const point=event.latlng.wrap();choose(point.lat,point.lng);});
 refresh();if(valid(latitude.value,longitude.value))map.setView([Number(latitude.value),Number(longitude.value)],17);else if(points.length)map.fitBounds(points,{padding:[20,20],maxZoom:16});
 const frame=requestAnimationFrame(()=>{if(!disposed)map.invalidateSize();});
 return {destroy(){disposed=true;cancelAnimationFrame(frame);latitude.removeEventListener('input',fromInputs);longitude.removeEventListener('input',fromInputs);map.remove();}};
};
