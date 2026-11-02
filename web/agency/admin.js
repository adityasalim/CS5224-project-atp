'use strict';
(async()=>{
const $=id=>document.getElementById(id),cfg=window.AGENCY_CONFIG;
const token=sessionStorage.getItem('id_token');
function adminSession(){
try {
 const claims=JSON.parse(atob(token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
 const end=Number(sessionStorage.getItem('expires_at')||0);
 return !!(token && Number.isFinite(end) && Date.now()<end && Number(claims.exp)*1000>Date.now() &&
  Array.isArray(claims['cognito:groups']) && claims['cognito:groups'].includes(window.AGENCY_CONFIG.ADMIN_GROUP));
} catch { return false; }
}
if(!adminSession()){location.replace('/');return;}
document.querySelector('main').hidden=false;
setInterval(()=>{if(!adminSession()){document.querySelector('main').hidden=true;location.replace('/');}},1000);
const el=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
async function request(path,body){if(!adminSession()){document.querySelector('main').hidden=true;location.replace('/');throw Error('Please sign in with an administrator account.');}const r=await fetch(cfg.API_BASE+path,{method:body?'POST':'GET',headers:{Authorization:token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});if(!r.ok)throw Error(r.status===403?'Administrator access required. Sign out and back in after your role is assigned.':'Request failed: '+r.status);return r.json();}
async function load(){try{
$('notice').textContent='Loading…';const [a,p]=await Promise.all([request('/admin/agencies/pending'),request('/admin/packages/pending')]);
for(const [id,rows,kind] of [['providers',a.agencies,'agencies'],['listings',p.packages,'packages']]){const root=$(id);root.replaceChildren();if(!rows.length)root.append(el('p','No pending entries.'));
for(const item of rows){const card=el('article','');card.append(el('h3',item.tradingName||item.title),el('p',item.providerType||item.listingType||'Agency package'));const details=el('pre',JSON.stringify(item,null,2));details.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';card.append(details);
for(const action of ['approve','reject']){const b=el('button',action==='approve'?'Approve':'Reject');b.onclick=async()=>{if(!confirm(action+' this entry?'))return;b.disabled=true;try{const identifier=kind==='agencies'?item.agencyId:item.packageId;await request('/admin/'+kind+'/'+encodeURIComponent(identifier)+'/'+action,{agencyId:item.agencyId});await load();}catch(e){$('notice').textContent=e.message;b.disabled=false;}};card.append(b);}root.append(card);}}
$('notice').textContent=(a.truncated||p.truncated)?'Queue is truncated; refresh after processing entries.':'Review the details before approving. Approve the provider before publishing its listings.';
}catch(e){$('notice').textContent=e.message;}}
$('refresh').onclick=load;await load();})();
