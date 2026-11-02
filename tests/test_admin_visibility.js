const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const page = fs.readFileSync('web/index.html','utf8');
assert.match(page, /id="providerApprovals"[^>]*class="hidden"[^>]*hidden/);
assert.match(page, /await handleCallback\(\);\s*updateProviderApprovals\(\)/);
assert.match(page, /setInterval\(updateProviderApprovals, 1000\)/);
const fn = page.match(/function updateProviderApprovals\(\) \{[\s\S]*?\n\}/)[0];
const now = Date.now(), future = Math.floor(now/1000)+3600;
for (const [label,user,on,visible] of [
 ['signed out',null,true,false], ['regular user',{exp:future},true,false],
 ['wrong group',{exp:future,'cognito:groups':['platform_admin']},true,false],
 ['string group',{exp:future,'cognito:groups':'tp-ahmed-marketplace-admin'},true,false],
 ['expired admin',{exp:1,'cognito:groups':['tp-ahmed-marketplace-admin']},true,false],
 ['signed-in admin',{exp:future,'cognito:groups':['tp-ahmed-marketplace-admin']},true,true],
 ['auth disabled',{exp:future,'cognito:groups':['tp-ahmed-marketplace-admin']},false,false]]) {
 const classes=new Set(['hidden']), link={hidden:true,classList:{toggle(k,yes){yes?classes.add(k):classes.delete(k);}}};
 vm.runInNewContext(fn+';updateProviderApprovals();',{currentUser:()=>user,$:()=>link,AUTH_ON:on,Date,Array,Number});
 assert.equal(!link.hidden,visible,label);assert.equal(!classes.has('hidden'),visible,label);
}
assert.match(fs.readFileSync('web/agency/admin.html','utf8'),/<main hidden>/);
const admin = fs.readFileSync('web/agency/admin.js','utf8');
async function portal(claims,expiry,expected) {
 const token=claims==='malformed'?'bad': 'header.'+Buffer.from(JSON.stringify(claims)).toString('base64url')+'.signature';
 let calls=0,redirect=null;
 const main={hidden:true},elements={notice:{textContent:''},refresh:{},providers:{replaceChildren(){},append(){}},listings:{replaceChildren(){},append(){}}};
 const context={setInterval:()=>0,sessionStorage:{getItem:k=>k==='id_token'?token:String(expiry)},Date,JSON,Number,Array,Error,Promise,
 atob:s=>Buffer.from(s,'base64').toString('binary'),location:{replace:p=>redirect=p},window:{AGENCY_CONFIG:{API_BASE:'https://api.example.test'}},
 document:{getElementById:id=>elements[id],querySelector:()=>main,createElement:()=>({})},
 fetch:async()=>{calls++;return {ok:true,json:async()=>({agencies:[],packages:[]})};}};
 await vm.runInNewContext(admin,context);
 assert.equal(calls,expected?2:0);assert.equal(main.hidden,!expected);assert.equal(redirect,expected?null:'/');
}
(async()=>{
 await portal({exp:future},now+3600000,false);
 await portal('malformed',now+3600000,false);
 await portal({exp:future,'cognito:groups':['tp-ahmed-marketplace-admin']},1,false);
 await portal({exp:1,'cognito:groups':['tp-ahmed-marketplace-admin']},now+3600000,false);
 await portal({exp:future,'cognito:groups':['tp-ahmed-marketplace-admin']},now+3600000,true);
 console.log('PASS admin link visibility and direct-page guards: signed out, regular, malformed, wrong-role, expired, admin');
})().catch(e=>{console.error(e);process.exitCode=1;});
