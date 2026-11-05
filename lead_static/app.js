const $ = selector => document.querySelector(selector);
const state = {period:30,profile:'all',status:'open',page:1,busy:false};
const initData = window.Telegram?.WebApp?.initData || '';
window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();

function escapeHTML(value=''){return String(value).replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));}
async function request(path){
  const response=await fetch(path,{headers:{Authorization:'tma '+initData},cache:'no-store'});
  let body={};try{body=await response.json();}catch{}
  if(!response.ok)throw new Error(body.error||'Не удалось загрузить данные.');
  return body;
}
function number(value){return new Intl.NumberFormat('ru-RU').format(value||0);}
function duration(seconds){
  if(seconds==null)return '—';
  const hours=Math.round(seconds/3600);
  if(hours<24)return hours+' ч';
  const days=Math.floor(hours/24),rest=hours%24;return rest?days+' д '+rest+' ч':days+' д';
}
function date(value){return value?new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}).format(new Date(value)):'—';}

function renderMetrics(data){
  const m=data.metrics;
  const cards=[['Открыто сейчас',m.open_now,'Текущий backlog'],['Создано',m.created,'За выбранный период'],['Завершено',m.completed_unique,'Уникальные закрытые тикеты'],['Медиана выполнения',duration(m.median_resolution_seconds),'От создания до закрытия'],['Переоткрыто',m.reopened,'События возврата в работу']];
  $('#metrics').innerHTML=cards.map(([label,value,hint])=>'<article class="metric"><span>'+label+'</span><strong>'+(typeof value==='number'?number(value):escapeHTML(value))+'</strong><small>'+hint+'</small></article>').join('');
}
function renderFlow(rows){
  const max=Math.max(1,...rows.flatMap(row=>[row.created,row.completed]));
  const visible=rows.length>45?rows.filter((_,index)=>index%2===0):rows;
  $('#flow').innerHTML=visible.map(row=>{
    const label=new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'short'}).format(new Date(row.date+'T12:00:00+03:00'));
    return '<div class="flow-day" data-label="'+label+': создано '+row.created+', завершено '+row.completed+'"><i class="bar created" style="height:'+Math.max(1,row.created/max*100)+'%"></i><i class="bar completed" style="height:'+Math.max(1,row.completed/max*100)+'%"></i></div>';
  }).join('');
}
function bars(target,rows){
  const max=Math.max(1,...rows.map(row=>row.value));
  $(target).innerHTML=rows.map(row=>'<div class="age-row"><div class="row-label"><span>'+escapeHTML(row.label)+'</span><strong>'+number(row.value)+'</strong></div><div class="track"><i style="width:'+(row.value/max*100)+'%"></i></div></div>').join('');
}
function renderBreakdowns(data){
  const age=data.age||{};
  bars('#age',[{label:'До суток',value:age.lt_1d||0},{label:'1–3 дня',value:age.d1_3||0},{label:'3–7 дней',value:age.d3_7||0},{label:'Больше недели',value:age.gte_7d||0}]);
  const profiles=data.profile==='all'?data.profiles:data.profiles.filter(item=>item.profile_key===data.profile);
  $('#profiles').innerHTML='<table class="profile-table"><thead><tr><th>Бот</th><th>Открыто</th><th>Создано</th><th>Закрыто</th></tr></thead><tbody>'+profiles.map(row=>'<tr><td>'+escapeHTML(row.profile_name)+'</td><td>'+number(row.open_now)+'</td><td>'+number(row.created)+'</td><td>'+number(row.completed)+'</td></tr>').join('')+'</tbody></table>';
  bars('#types',data.types.map(row=>({label:row.ticket_type_name,value:row.count})));
}
async function loadSummary(){
  $('#notice').textContent='';
  try{const data=await request('api/summary?period='+state.period+'&profile='+encodeURIComponent(state.profile));renderMetrics(data);renderFlow(data.flow);renderBreakdowns(data);}
  catch(error){$('#notice').textContent=error.message;$('#metrics').innerHTML='';}
}
async function loadTickets(){
  $('#tickets').innerHTML='<p class="loading">Загрузка тикетов…</p>';$('#pagination').innerHTML='';
  try{
    const data=await request('api/tickets?status='+state.status+'&profile='+encodeURIComponent(state.profile)+'&page='+state.page);
    $('#tickets').innerHTML=data.items.map(ticket=>'<button class="ticket-row" data-ticket="'+ticket.ticket_id+'"><span class="id">#'+ticket.ticket_id+'</span><span class="profile">'+escapeHTML(ticket.profile_name)+'</span><span class="title">'+escapeHTML(ticket.title||ticket.description.split('\n')[0])+'</span><time>'+date(ticket.closed_at||ticket.updated_at)+'</time></button>').join('')||'<p class="empty">Тикетов с таким статусом нет.</p>';
    document.querySelectorAll('[data-ticket]').forEach(button=>button.onclick=()=>openTicket(Number(button.dataset.ticket)));
    $('#pagination').innerHTML=(data.has_previous?'<button data-page="'+(state.page-1)+'">Назад</button>':'')+(data.has_next?'<button data-page="'+(state.page+1)+'">Дальше</button>':'');
    document.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{state.page=Number(button.dataset.page);loadTickets();});
  }catch(error){$('#tickets').innerHTML='<p class="empty">'+escapeHTML(error.message)+'</p>';}
}
async function openTicket(id){
  const dialog=$('#ticket-dialog');$('#ticket-detail').innerHTML='<p class="loading">Загрузка карточки…</p>';dialog.showModal();
  try{
    const ticket=await request('api/tickets/'+id);
    $('#ticket-detail').innerHTML='<span class="eyebrow">'+escapeHTML(ticket.profile_name)+' · '+escapeHTML(ticket.ticket_type_name)+'</span><h2 class="detail-title">#'+ticket.ticket_id+' — '+escapeHTML(ticket.title||ticket.description.split('\n')[0])+'</h2><p class="detail-meta">'+escapeHTML(ticket.status_name)+' · создан '+date(ticket.created_at)+(ticket.closed_at?' · завершён '+date(ticket.closed_at):'')+'</p><div class="detail-body">'+escapeHTML(ticket.description)+'</div><section class="conversation"><span class="eyebrow">ПЕРЕПИСКА И ЗАМЕТКИ</span>'+ticket.conversation.map(message=>'<article class="message '+(message.visibility==='internal'?'internal':'')+'"><header><span>'+(message.visibility==='internal'?'Внутренняя заметка':escapeHTML(message.author_role))+'</span><time>'+date(message.sent_at)+'</time></header><p>'+escapeHTML(message.body)+'</p></article>').join('')+'</section>';
  }catch(error){$('#ticket-detail').innerHTML='<p class="empty">'+escapeHTML(error.message)+'</p>';}
}
$('#dialog-close').onclick=()=>$('#ticket-dialog').close();
$('#ticket-dialog').addEventListener('click',event=>{if(event.target===$('#ticket-dialog'))$('#ticket-dialog').close();});
$('#period').onchange=event=>{state.period=Number(event.target.value);loadSummary();};
$('#profile').onchange=event=>{state.profile=event.target.value;state.page=1;loadSummary();loadTickets();};
document.querySelectorAll('[data-status]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-status]').forEach(item=>item.classList.toggle('active',item===button));state.status=button.dataset.status;state.page=1;loadTickets();});
loadSummary();loadTickets();
