/* Shared confirmations and unsaved form protection. */
(() => {
const dialog=document.createElement('dialog');dialog.id='confirmation-dialog';dialog.className='confirmation-dialog';dialog.setAttribute('aria-labelledby','confirmation-title');dialog.setAttribute('aria-describedby','confirmation-message');
const title=document.createElement('h2');title.id='confirmation-title';const message=document.createElement('p');message.id='confirmation-message';const actions=document.createElement('div');actions.className='actions';const cancel=document.createElement('button');cancel.type='button';cancel.className='secondary';cancel.textContent='Hủy';const approve=document.createElement('button');approve.type='button';actions.append(cancel,approve);dialog.append(title,message,actions);document.body.append(dialog);
let pending=null;const guards=new Set();
function finish(value){if(!pending)return;const resolve=pending;pending=null;dialog.close();resolve(value);}
cancel.onclick=()=>finish(false);approve.onclick=()=>finish(true);dialog.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
window.confirmAction=({title:heading='Xác nhận thao tác',message:body,confirmText='Xác nhận',danger=false}={})=>{
 if(pending)return Promise.resolve(false);
 cancel.disabled=false;approve.disabled=false;title.textContent=heading;message.textContent=body||'Bạn có muốn tiếp tục?';approve.textContent=confirmText;approve.className=danger?'danger':'';
 return new Promise(resolve=>{pending=resolve;dialog.showModal();cancel.focus();});
};
function snapshot(form){return JSON.stringify([...form.elements].filter(e=>['INPUT','SELECT','TEXTAREA'].includes(e.tagName)&&e.type!=='hidden').map(e=>[e.name||e.id,e.type==='file'?[...e.files].map(f=>[f.name,f.size,f.lastModified]):['checkbox','radio'].includes(e.type)?e.checked:e.value]));}
window.watchFormChanges=form=>{
 let baseline=snapshot(form);const container=form.closest('dialog');
 const guard={markClean(){baseline=snapshot(form);},isDirty(){return (!container||container.open)&&snapshot(form)!==baseline;},async confirmDiscard(){return !guard.isDirty()||await confirmAction({title:'Bỏ thay đổi chưa lưu?',message:'Các thay đổi bạn vừa nhập chưa được lưu. Bạn có muốn bỏ thay đổi và tiếp tục?',confirmText:'Bỏ thay đổi',danger:true});}};
 guards.add(guard);form.addEventListener('reset',()=>queueMicrotask(()=>guard.markClean()));return guard;
};
function dirty(){return [...guards].some(g=>g.isDirty());}
window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
document.addEventListener('click',async e=>{
 const link=e.target.closest('a[href]');if(!link||e.defaultPrevented||e.button!==0||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
 const url=new URL(link.href,location.href);if(url.origin!==location.origin||url.pathname.startsWith('/api/')||(url.pathname===location.pathname&&url.search===location.search&&link.getAttribute('href').startsWith('#')))return;
 if(!dirty())return;e.preventDefault();if(await confirmAction({title:'Rời trang này?',message:'Bạn có thay đổi chưa lưu. Rời trang sẽ bỏ các thay đổi này.',confirmText:'Rời trang',danger:true})){guards.forEach(g=>g.markClean());location.assign(url.href);}
});
document.querySelectorAll('form[action="/logout"]').forEach(form=>form.addEventListener('submit',async e=>{e.preventDefault();if(await confirmAction({title:'Đăng xuất?',message:dirty()?'Bạn có thay đổi chưa lưu. Đăng xuất sẽ bỏ các thay đổi này.':'Bạn có muốn đăng xuất khỏi tài khoản hiện tại?',confirmText:'Đăng xuất',danger:dirty()})){guards.forEach(g=>g.markClean());HTMLFormElement.prototype.submit.call(form);}}));
})();
