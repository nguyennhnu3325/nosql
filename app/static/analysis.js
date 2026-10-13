(() => {
const $=id=>document.getElementById(id);
const fmt=value=>Number(value).toLocaleString('vi-VN',{maximumFractionDigits:2});
const round=value=>Math.round(value*100)/100;
function table(id,rows,keys,empty='Không có dữ liệu.'){
 const host=$(id);let page=0;const size=20,pages=Math.max(1,Math.ceil(rows.length/size));
 const pager=document.createElement('div');pager.className='pagination';
 const previous=document.createElement('button'),next=document.createElement('button'),status=document.createElement('span');
 previous.type=next.type='button';previous.className=next.className='secondary';previous.textContent='← Trước';next.textContent='Sau →';
 pager.append(previous,status,next);
 if(rows.length>size)host.closest('.scroll').after(pager);
 function draw(){host.replaceChildren();for(const item of rows.slice(page*size,(page+1)*size)){const row=host.insertRow();for(const key of keys)cell(row,key==='degree'?fmt(item[key]):item[key]??'—');}if(!rows.length)cell(host.insertRow(),empty).colSpan=keys.length;previous.disabled=page===0;next.disabled=page===pages-1;status.textContent='Trang '+(page+1)+' / '+pages+' · '+fmt(rows.length)+' dòng';}
 previous.onclick=()=>{page--;draw();};next.onclick=()=>{page++;draw();};draw();
}
function columns(id,rows,unit){
 const host=$(id);host.replaceChildren();const max=Math.max(1,...rows.map(row=>row.count));
 if(!rows.some(row=>row.count)){host.textContent='Chưa có dữ liệu.';return;}
 const plot=document.createElement('div');plot.className='analysis-columns';
 for(const row of rows){
  const column=document.createElement('div');column.className='analysis-column';
  const value=document.createElement('span');value.className='analysis-column-value';value.textContent=fmt(row.count);
  const track=document.createElement('div');track.className='analysis-column-track';
  const bar=document.createElement('div');bar.className='analysis-column-bar';bar.style.height=row.count/max*100+'%';track.append(bar);
  const label=document.createElement('span');label.className='analysis-column-label';label.textContent=row.type;label.title=row.type;
  column.title=row.type+': '+fmt(row.count)+' '+unit;column.append(value,track,label);plot.append(column);
 }
 host.append(plot);
}
function pie(rows){
 const host=$('direction-chart');host.replaceChildren();const known=rows.filter(row=>row.type==='true'||row.type==='false'),total=known.reduce((sum,row)=>sum+row.count,0);
 const unknown=rows.filter(row=>row.type!=='true'&&row.type!=='false').reduce((sum,row)=>sum+row.count,0);
 $('oneway-note').textContent='Tỷ lệ tính trên '+fmt(total)+' ROAD có oneway hợp lệ. '+fmt(unknown)+' ROAD thiếu hoặc sai oneway không tính vào tỷ lệ.';
 if(!total){host.textContent='Chưa có ROAD có oneway hợp lệ.';return;}
 const share=(known.find(row=>row.type==='true')?.count||0)/total*100;
 const disk=document.createElement('div');disk.className='analysis-pie';disk.style.background='conic-gradient(#17866b 0% '+share+'%, #8b6cb8 '+share+'% 100%)';disk.setAttribute('role','img');disk.setAttribute('aria-label','Một chiều '+fmt(share)+'%, hai chiều '+fmt(100-share)+'%');
 const legend=document.createElement('div');legend.className='analysis-pie-legend';
 for(const row of known){const item=document.createElement('div'),dot=document.createElement('i'),text=document.createElement('span');dot.style.background=row.type==='true'?'#17866b':'#8b6cb8';text.textContent=(row.type==='true'?'Một chiều (true)':'Hai chiều (false)')+': '+fmt(row.count)+' ROAD · '+fmt(row.count/total*100)+'%';item.append(dot,text);legend.append(item);}
 host.append(disk,legend);
}
function render(n){
 const {overview:o,length:l,degree:d,connectivity:c,issues:i}=n;
 renderMetrics('metrics',[['Tổng số Junction',o.junctions],['Tổng số ROAD',o.roads],['Tổng số Place',o.places],['Tổng số ACCESS_TO',o.access]]);
 renderMetrics('length-metrics',[['Tổng chiều dài ROAD (km)',round(l.totalKm)],['Chiều dài trung bình (m)',round(l.averageM)]]);
 renderMetrics('degree-metrics',[['Degree trung bình',round(d.average)],['Degree cao nhất',d.maximum],['Junction cô lập',d.isolated]]);
 renderMetrics('connectivity-metrics',[['Connected components',c.count],['Component lớn nhất (Junction)',c.largest]]);
 columns('road-chart',n.highway,'ROAD');pie(n.oneway);columns('degree-histogram',d.histogram,'Junction');
 table('top-junctions',d.top,['junctionId','degree']);
 for(const [id,countId,rows,keys] of [
  ['unlinked-places','unlinked-count',i.unlinkedPlaces,['placeId','name','category']],
  ['missing-length-roads','missing-length-count',i.missingLengthRoads,['roadId','name','source','target']],
  ['self-loop-roads','self-loop-count',i.selfLoopRoads,['roadId','name','source','target']],
  ['missing-coordinate-junctions','missing-coordinate-count',i.missingCoordinateJunctions,['junctionId','latitude','longitude']]
 ]){$(countId).textContent='('+fmt(rows.length)+')';table(id,rows,keys);}
}
api('/api/analysis/details').then(data=>{render(data.network);message('Đã tải phân tích mạng lưới.');}).catch(error=>message(error.message,true));
})();
