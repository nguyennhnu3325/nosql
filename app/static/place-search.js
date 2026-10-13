/* Dropdown selector with a searchable list; saved values remain Place IDs. */
window.enablePlaceSearch=function(select){
 const wrap=document.createElement('div');wrap.className='place-select';
 const trigger=document.createElement('button');trigger.type='button';trigger.className='place-select-trigger';trigger.id=select.id+'Search';trigger.disabled=select.disabled;trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-haspopup','listbox');
 const caption=document.createElement('span'),arrow=document.createElement('span');arrow.textContent='▾';arrow.setAttribute('aria-hidden','true');trigger.append(caption,arrow);
 const panel=document.createElement('div');panel.className='place-select-panel';panel.hidden=true;
 const search=document.createElement('input');search.type='search';search.placeholder='Nhập tên địa điểm để tìm...';search.setAttribute('aria-label','Tìm địa điểm');search.autocomplete='off';
 const list=document.createElement('div');list.className='place-select-list';list.id=select.id+'Suggestions';list.setAttribute('role','listbox');trigger.setAttribute('aria-controls',list.id);
 const choices=[...select.options].filter(o=>o.value).map(o=>({id:o.value,label:o.text}));
 const normalize=text=>text.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/\u0111/g,'d');
 let matches=[],active=-1;
 function close(focus=false){panel.hidden=true;trigger.setAttribute('aria-expanded','false');if(focus)trigger.focus();}
 function choose(choice){select.value=choice.id;caption.textContent=choice.label;trigger.title=choice.label;select.dispatchEvent(new Event('change',{bubbles:true}));close(true);}
 function render(){
  list.replaceChildren();matches=choices.filter(c=>normalize(c.label+' '+c.id).includes(normalize(search.value.trim())));active=-1;
  for(const choice of matches){const button=document.createElement('button');button.type='button';button.className='place-select-option';button.setAttribute('role','option');button.setAttribute('aria-selected',String(choice.id===select.value));button.textContent=choice.label;button.title=choice.label+' · '+choice.id;button.onclick=()=>choose(choice);list.append(button);}
  if(!matches.length){const empty=document.createElement('p');empty.className='hint';empty.textContent='Không tìm thấy địa điểm phù hợp.';list.append(empty);}
 }
 function open(){if(trigger.disabled)return;panel.hidden=false;trigger.setAttribute('aria-expanded','true');search.value='';render();search.focus();}
 trigger.onclick=()=>panel.hidden?open():close();
 trigger.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();open();}});
 search.addEventListener('input',render);
 panel.addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();close(true);return;}
  if(['ArrowDown','ArrowUp'].includes(event.key)&&matches.length){event.preventDefault();active=(active+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length;list.children[active].focus();}
  if(event.key==='Enter'&&event.target===search){event.preventDefault();if(matches.length)choose(matches[active<0?0:active]);}
 });
 document.addEventListener('click',event=>{if(!wrap.contains(event.target))close();});
 wrap.addEventListener('focusout',event=>{if(event.relatedTarget&&!wrap.contains(event.relatedTarget))close();});
 select.addEventListener('change',()=>{const choice=choices.find(c=>c.id===select.value);caption.textContent=choice?.label||'Chọn địa điểm...';trigger.title=caption.textContent;});
 caption.textContent=choices.find(c=>c.id===select.value)?.label||'Chọn địa điểm...';
 const label=document.querySelector('label[for="'+select.id+'"]');if(label){label.htmlFor=trigger.id;label.id=select.id+'Label';trigger.setAttribute('aria-labelledby',label.id+' '+trigger.id);list.setAttribute('aria-labelledby',label.id);}
 select.hidden=true;select.required=false;panel.append(search,list);wrap.append(trigger,panel);select.after(wrap);return trigger;
};
