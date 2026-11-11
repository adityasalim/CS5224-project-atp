const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../web/index.html','utf8');
const start=source.indexOf('function renderPackageOptions('),end=source.indexOf('/* ---------- end agency package options ---------- */',start);
function el(tag,cls,text){return {tag,cls,text:text||'',style:{},children:[],append(...c){this.children.push(...c)}}}
const context={el,tripMoney:(n,c)=>c+' '+n,packageEnquiryUrl:()=>null};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
function text(node){return typeof node==='string'?node:[node.text,...node.children.map(text)].join(' ')}
const scope={type:'city_leg',city:'Tokyo',startDate:'2030-01-16',endDate:'2030-01-21',flightCoverage:'inbound_only'};
const row={title:'Tokyo 6 Days',agencyName:'Agency',coverageScope:scope,comparison:{currency:'SGD',packageTotal:'3600',knownExtras:'0',knownMinimumTotal:'3600',costComplete:false,missingCosts:['Remaining trip'],coverageScope:scope},reasons:['Match score: 80%'],tradeoffs:[]};
for(const [mode,status] of [['compare','recommended'],['compare','eligible_options'],['packages_only','recommended']]){
 const output=text(context.renderPackageOptions({mode,status,packages:[row]},{}));
 assert(output.includes('Tokyo 6 Days'));assert(output.includes('Partial-trip option: Tokyo only'));
 assert(output.includes('Full trip total unknown'));
 if(status==='eligible_options')assert(output.includes('reviewer preferred the custom itinerary'));
}
// Even stale package entries must not show if the result is custom-only.
assert.equal(text(context.renderPackageOptions({mode:'custom_only',status:'custom_only',packages:[row]},{})).trim(),'');
assert.equal(text(context.renderPackageOptions({mode:'custom_only',status:'recommended',packages:[row]},{})).trim(),'');
console.log('PASS: chat displays packages in both agency modes; custom-only hides them, including stale entries');
