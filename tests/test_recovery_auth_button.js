const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/../web/index.html','utf8');
const recovery=source.slice(source.indexOf('function showRunRecovery()'),source.indexOf('function applyBackgroundStatus('));
assert(!recovery.includes('Sign in again'));
assert(recovery.includes('Check request status'));
const authFunctions=source.slice(source.indexOf('function showSignInRecovery()'),source.indexOf('/* ---------- helpers ---------- */'));
let currentButton=null, removed=0, signins=0;
let transport=async()=>({ok:true});
const context={AUTH_ON:true,auth:{fetch:(...a)=>transport(...a)},signIn:()=>signins++,
  $:id=>id==='reauthBtn'?currentButton:{parentNode:{append:b=>{currentButton=b}}},
  el:()=>({remove:()=>{currentButton=null;removed++}})};
vm.createContext(context);vm.runInContext(authFunctions,context);
(async()=>{
  await context.apiFetch('/status');assert.equal(currentButton,null);
  transport=async()=>{throw Object.assign(new Error('network'),{code:'NETWORK'})};
  await assert.rejects(()=>context.apiFetch('/status'));assert.equal(currentButton,null);
  transport=async()=>{throw Object.assign(new Error('expired'),{code:'SESSION_EXPIRED'})};
  await assert.rejects(()=>context.apiFetch('/status'));assert(currentButton);
  currentButton.onclick();assert.equal(signins,1);
  const original=currentButton;
  await assert.rejects(()=>context.apiFetch('/status'));assert.equal(currentButton,original);
  transport=async()=>({ok:true});await context.apiFetch('/status');assert.equal(currentButton,null);assert.equal(removed,1);
  // Also compile every inline script to catch unrelated syntax breakage.
  for(const match of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
  console.log('PASS: no login during normal work/network failure; expired auth shows one recovery control; success removes it');
})().catch(error=>{console.error(error);process.exitCode=1});
