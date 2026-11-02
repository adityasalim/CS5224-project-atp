const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
(async()=>{
const win=new JSDOM(fs.readFileSync('web/agency/index.html','utf8'),{url:'https://example.test/agency/index.html',runScripts:'outside-only'}).window;
win.AGENCY_CONFIG={API_BASE:'https://api.example.test',TERMS_URL:'https://example.test/agency/terms.html'};
win.sessionStorage.setItem('id_token','synthetic-token');win.sessionStorage.setItem('expires_at',String(Date.now()+100000));
let registered=null;win.fetch=async(url,opts)=>{if(opts.method==='POST'){registered=JSON.parse(opts.body);return{ok:true,json:async()=>({})};}return registered?{ok:true,json:async()=>({agency:{...registered,status:'PENDING_REVIEW'}})}:{ok:false,status:404,json:async()=>({error:'not_registered'})};};
win.eval(fs.readFileSync('web/agency/app.js','utf8'));await new Promise(r=>setTimeout(r,10));
assert.equal(win.document.getElementById('registration').hidden,false);
const f=win.document.getElementById('registerForm');
for(const key of ['legalName','tradingName','contactPerson','telephone','country','businessAddress','description','destinations'])f.elements[key].value='Test';
f.elements.businessEmail.value='guide@example.invalid';f.elements.providerType.value='local_guide';f.elements.languages.value='English, Japanese';f.elements.experienceDescription.value='Synthetic test experience';f.elements.termsAccepted.checked=true;
f.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,15));
assert.equal(registered.providerType,'local_guide');assert.deepEqual(registered.languages,['English','Japanese']);assert(!('website'in registered));assert.equal(win.document.getElementById('dashboard').hidden,false);assert.equal(win.document.getElementById('newPackage').disabled,true);
assert(win.document.querySelector('[name=flightsIncluded]').value==='no');
console.log('PASS provider registration: guide profile, optional blank fields, pending restrictions, flights optional');win.close();
})();
