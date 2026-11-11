'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const config = window.AGENCY_CONFIG || {};
  let agency = null, items = [];
  const token = () => { try { return Date.now() < Number(sessionStorage.getItem('expires_at') || 0) ? sessionStorage.getItem('id_token') : null; } catch { return null; } };
  const parseList = value => String(value || '').split(/[,\n]/).map(s => s.trim()).filter(Boolean);
  const value = (form, key) => String(form.elements[key].value || '').trim();
  function validBase(base) {
    try { const u = new URL(base); return u.protocol === 'https:' && !u.username && !u.password && !u.search && !u.hash; } catch { return false; }
  }
  function notice(message, error = false) { const n = $('notice'); n.className = 'notice' + (error ? ' error' : ''); n.textContent = message; n.hidden = !message; }
  function show(id) { for (const key of ['signin','unavailable','registration','dashboard','profileEditor','packageEditor']) $(key).hidden = key !== id; }
  function element(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  function action(label, fn, primary = false) { const b = element('button', label, primary ? 'primary' : ''); b.type = 'button'; b.addEventListener('click', fn); return b; }
  async function request(path, method = 'GET', body) {
    if (!token()) { show('signin'); throw new Error('Your session expired. Please sign in again.'); }
    if (!validBase(config.API_BASE)) throw new Error('Partner service is not configured.');
    const headers = { Authorization: token(), Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (body) for (const key of ['website','registrationNumber']) if (body[key]==='') delete body[key];
    const res = await fetch(config.API_BASE.replace(/\/$/, '') + path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store'});
    let data = {}; try { data = await res.json(); } catch { /* Return a generic error if the service gives no JSON. */ }
    if (!res.ok) { const e = new Error(typeof data.message === 'string' ? data.message : 'Request failed (' + res.status + ').'); e.status = res.status; throw e; }
    return data;
  }
  function reportError(e) { notice(e && e.message || 'Something went wrong. Please try again.', true); }
  async function perform(button, fn) { button.disabled = true; notice(''); try { await fn(); } catch (e) { reportError(e); } finally { button.disabled = false; } }
  function readProfile(form) { return {tradingName:value(form,'tradingName'), contactPerson:value(form,'contactPerson'), businessEmail:value(form,'businessEmail'), telephone:value(form,'telephone'), country:value(form,'country'), businessAddress:value(form,'businessAddress'), businessDescription:value(form,'description'), website:value(form,'website'), supportedDestinations:parseList(value(form,'destinations')), contactPreferences:parseList(value(form,'contactPreferences'))}; }
  function readPackage(form) { const original = items.find(p => p.packageId === value(form, "packageId")) || {}; const destinations = [{city:value(form,'city'),country:value(form,'country'),nights:Number(value(form,'nights'))}]; for (const line of value(form,'additionalDestinations').split('\n').map(x => x.trim()).filter(Boolean)) { const parts = line.split('|').map(x => x.trim()); if (parts.length !== 3 || !parts[0] || !parts[1] || !/^\d+$/.test(parts[2])) throw new Error('Use city | country | nights for each additional destination.'); destinations.push({city:parts[0], country:parts[1], nights:Number(parts[2])}); } return PackageFields.read(form, {...original, title:value(form,'title'),description:value(form,'description'),destinations,durationDays:Number(value(form,'durationDays')),price:{amount:Number(value(form,'amount')),currency:value(form,'currency'),basis:value(form,'basis')},minimumTravellers:Number(value(form,'minimumTravellers')),maximumTravellers:Number(value(form,'maximumTravellers')),availability:[{startDate:value(form,'startDate'),endDate:value(form,'endDate')}],included:parseList(value(form,'included')),excluded:parseList(value(form,'excluded')),categories:parseList(value(form,'categories')),cuisines:parseList(value(form,'cuisines')),listingType:value(form,'listingType'),...(value(form,'listingType')==='guided_tour'?{durationHours:Number(value(form,'durationHours')),meetingPoint:value(form,'meetingPoint'),serviceArea:value(form,'serviceArea'),languages:parseList(value(form,'tourLanguages'))}:{}),costInclusions:{flights:value(form,'flightsIncluded')==='yes'},flightType:value(form,'flightsIncluded')==='yes'?value(form,'flightType'):'none',departureLocations:parseList(value(form,'departureLocations'))}); }
  function setFormValue(form, key, val) { form.elements[key].value = Array.isArray(val) ? val.join(', ') : val == null ? '' : String(val); }
  function openProfile() { const f = $('profileForm'); for (const k of ['tradingName','contactPerson','businessEmail','telephone','country','businessAddress','website']) setFormValue(f, k, agency[k]); setFormValue(f,'description',agency.businessDescription); setFormValue(f,'destinations',agency.supportedDestinations); setFormValue(f,'contactPreferences',agency.contactPreferences); show('profileEditor'); }
  function openPackage(pkg) {
    const f = $('packageForm'); f.reset(); const p = pkg || {};
    const destination = (p.destinations || [])[0] || {}, price = p.price || {}, dates = (p.availability || [])[0] || {};
    for (const key of ['packageId','title','description','durationDays','minimumTravellers','maximumTravellers','bookingUrl']) if (p[key] != null) setFormValue(f,key,p[key]);
    for (const key of ['city','country','nights']) if (destination[key] != null) setFormValue(f,key,destination[key]); setFormValue(f,'additionalDestinations',(p.destinations || []).slice(1).map(d => [d.city,d.country,d.nights].join(' | ')).join('\n'));
    for (const key of ['amount','currency','basis']) if (price[key] != null) setFormValue(f,key,price[key]);
    for (const key of ['startDate','endDate']) if (dates[key] != null) setFormValue(f,key,dates[key]);
    for (const key of ['included','excluded','categories','cuisines']) if (p[key]) setFormValue(f,key,p[key]);
    for (const key of ['listingType','durationHours','meetingPoint','serviceArea','flightType']) if(p[key]!=null)setFormValue(f,key,p[key]);
    setFormValue(f,'tourLanguages',p.languages || []);setFormValue(f,'departureLocations',p.departureLocations || []);setFormValue(f,'flightsIncluded',p.costInclusions?.flights===true?'yes':'no');
    PackageFields.load(f, p);
    $('packageHeading').textContent = pkg ? 'Edit package' : 'Create package'; show('packageEditor');
  }
  async function transition(id, actionName, button) { await perform(button, async () => { await request('/agency/packages/' + encodeURIComponent(id) + '/' + actionName, 'POST', {}); await loadDashboard(); notice('Package updated.'); }); }
  function renderPackages() {
    const container = $('packages'); container.replaceChildren();
    if (!items.length) { container.append(element('p','You have no packages yet.')); return; }
    for (const pkg of items) {
      const row = element('article'); row.append(element('h3',pkg.title || 'Untitled package'));
      const destinations = (pkg.destinations || []).map(d => d.city).filter(Boolean).join(' → ');
      const price = pkg.price || {}; row.append(element('p', [pkg.status || 'DRAFT', destinations, price.currency && price.amount != null ? price.currency + ' ' + price.amount : ''].filter(Boolean).join(' · '), 'package-meta'));
      const actions = element('div',null,'actions'), approved = agency.status === 'APPROVED';
      if (approved && ['DRAFT','REJECTED'].includes(pkg.status)) { actions.append(action('Edit', () => openPackage(pkg))); const b=action('Submit for review', () => transition(pkg.packageId,'submit',b)); actions.append(b); }
      if (approved && pkg.status === 'PUBLISHED') { const b=action('Pause', () => transition(pkg.packageId,'pause',b)); actions.append(b); }
      if (approved && ['DRAFT','PENDING_REVIEW','PUBLISHED','PAUSED'].includes(pkg.status)) { const b=action('Withdraw', () => transition(pkg.packageId,'withdraw',b)); actions.append(b); }
      row.append(actions); container.append(row);
    }
  }
  function renderDashboard() {
    $('status').textContent = (agency.status || 'PENDING_REVIEW').replaceAll('_',' ');
    $('agencyName').textContent = agency.tradingName || agency.legalName || 'Agency';
    $('agencyDetails').textContent = [agency.country, agency.businessEmail].filter(Boolean).join(' · ');
    $('newPackage').disabled = agency.status !== 'APPROVED';
    const stats = $('stats'); stats.replaceChildren();
    for (const [label,status] of [['Total',null],['Draft','DRAFT'],['Published','PUBLISHED'],['Expired','EXPIRED']]) { const cell=element('div'); cell.append(element('strong',String(status ? items.filter(p => p.status === status).length : items.length)),element('span',label)); stats.append(cell); }
    $('enquiries').textContent = 'Traveller enquiries open an email draft to your registered contact address. Check that inbox; enquiries are not stored in this dashboard.';
    renderPackages(); show('dashboard');
  }
  async function loadDashboard() { if (agency.status !== 'APPROVED') { items = []; renderDashboard(); return; } const data = await request('/agency/packages'); items = Array.isArray(data) ? data : Array.isArray(data.packages) ? data.packages : []; renderDashboard(); }
  async function initialise() {
    notice(''); if (!token()) { $('status').textContent='Signed out'; show('signin'); return; }
    if (!validBase(config.API_BASE)) { $('status').textContent='Unavailable'; show('unavailable'); return; }
    try { const data=await request('/agency/me'); agency=data.agency || data; await loadDashboard(); }
    catch (e) { if (e.status === 404) { agency = null; $('status').textContent='Not registered'; show('registration'); } else { $('status').textContent='Unavailable'; show('unavailable'); reportError(e); } }
  }
  $('signout').addEventListener('click',() => { for (const k of ['id_token','access_token','expires_at','session_id','transcript']) sessionStorage.removeItem(k); location.assign('/'); });
  $('refresh').addEventListener('click', () => perform($('refresh'), initialise));
  $('editProfile').addEventListener('click', openProfile);
  $('newPackage').addEventListener('click', () => openPackage(null));
  for (const b of document.querySelectorAll('.cancel')) b.addEventListener('click', () => { notice(''); renderDashboard(); });
  $('registerForm').addEventListener('submit', event => { event.preventDefault(); const f=event.currentTarget,b=f.querySelector('button[type=submit]'); perform(b, async () => { if (!validBase(config.TERMS_URL)) throw new Error('Marketplace terms are not yet available. Registration is not open.'); const payload={...readProfile(f),providerType:value(f,'providerType'),languages:parseList(value(f,'languages')),experienceDescription:value(f,'experienceDescription'),legalName:value(f,'legalName'),registrationNumber:value(f,'registrationNumber'),termsAccepted:f.elements.termsAccepted.checked}; await request('/agency/register','POST',payload); await initialise(); notice('Registration submitted for review.'); }); });
  $('profileForm').addEventListener('submit', event => { event.preventDefault(); const f=event.currentTarget,b=f.querySelector('button[type=submit]'); perform(b, async () => { await request('/agency/me','PUT',{...readProfile(f),providerType:agency.providerType || 'agency',languages:agency.languages || [],experienceDescription:agency.experienceDescription || '',legalName:agency.legalName,registrationNumber:agency.registrationNumber || '',termsAccepted:true}); await initialise(); notice('Profile updated.'); }); });
  $('packageForm').addEventListener('submit', event => { event.preventDefault(); const f=event.currentTarget,b=f.querySelector('button[type=submit]'); perform(b, async () => { const p=readPackage(f), id=value(f,'packageId'); if (p.minimumTravellers > p.maximumTravellers) throw new Error('Maximum travellers must be at least the minimum.'); if (p.availability[0].endDate < p.availability[0].startDate) throw new Error('End date must be on or after start date.'); if (p.destinations.reduce((sum, d) => sum + d.nights, 0) >= p.durationDays) throw new Error('Nights must be fewer than duration days.'); await request('/agency/packages'+(id ? '/'+encodeURIComponent(id) : ''),id ? 'PUT' : 'POST',p); await loadDashboard(); notice('Draft saved.'); }); });
  if (validBase(config.TERMS_URL)) { const link=element('a','Read marketplace terms'); link.href=config.TERMS_URL; link.target='_blank'; link.rel='noopener noreferrer'; $('registerForm').querySelector('.hint').replaceChildren(link); }
  initialise();
})();

