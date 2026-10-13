/* Keep Leaflet aligned with responsive layouts and report failed background tiles. */
window.attachMapBackground=function(map){
 const container=map.getContainer();
 const notice=document.createElement('div');notice.className='map-load-status';notice.hidden=true;notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const label=document.createElement('span'),retry=document.createElement('button');retry.type='button';retry.className='secondary';retry.textContent='Tải lại nền bản đồ';notice.append(label,retry);container.before(notice);
 const failed=new Set();let frame=0,observer=null,lastWidth=0,lastHeight=0;
 const key=e=>e.coords?[e.coords.z,e.coords.x,e.coords.y].join('/'):(e.tile?.src||'unknown');
 const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,keepBuffer:3,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
 function status(){notice.hidden=!failed.size&&navigator.onLine!==false;if(!notice.hidden)label.textContent=navigator.onLine===false?'Mất kết nối mạng. Dữ liệu giao thông vẫn được hiển thị.':'Một số ô nền bản đồ chưa tải được. Kiểm tra kết nối hoặc tải lại nền bản đồ.';}
 tiles.on('tileerror',e=>{failed.add(key(e));status();});tiles.on('tileload',e=>{failed.delete(key(e));status();});tiles.on('tileunload',e=>{failed.delete(key(e));status();});tiles.on('load',status);
 function refreshSize(){cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{if(!container.isConnected)return;const width=container.clientWidth,height=container.clientHeight;if(width&&height&&(width!==lastWidth||height!==lastHeight)){lastWidth=width;lastHeight=height;map.invalidateSize({pan:false,debounceMoveend:true});}});}
 function reload(){failed.clear();notice.hidden=true;map.invalidateSize({pan:false});tiles.redraw();if(navigator.onLine===false)status();}
 retry.onclick=reload;
 if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(refreshSize);observer.observe(container);}
 window.addEventListener('resize',refreshSize);window.addEventListener('load',refreshSize);window.addEventListener('online',reload);window.addEventListener('offline',status);
 document.addEventListener('visibilitychange',onVisible);function onVisible(){if(!document.hidden)refreshSize();}
 map.on('unload',()=>{observer?.disconnect();cancelAnimationFrame(frame);window.removeEventListener('resize',refreshSize);window.removeEventListener('load',refreshSize);window.removeEventListener('online',reload);window.removeEventListener('offline',status);document.removeEventListener('visibilitychange',onVisible);notice.remove();});
 tiles.addTo(map);map.whenReady(refreshSize);status();return tiles;
};
