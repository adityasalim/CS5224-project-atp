const fs = require('node:fs');
const vm = require('node:vm');
const PackageFields = require('../web/agency/package-fields.js');
const html = fs.readFileSync(__dirname + '/../web/agency/index.html', 'utf8');
const form = {elements: Object.fromEntries([...html.matchAll(/name="([^"]+)"/g)].map(m => [m[1], {value: ''}]))};
PackageFields.load(form, {});
const data = {
 title:'Test Tokyo',description:'Tokyo culture and food with all services included',city:'Tokyo',country:'Japan',nights:'4',durationDays:'5',
 amount:'1800',currency:'SGD',basis:'per_person',minimumTravellers:'1',maximumTravellers:'4',
 startDate:'2030-01-01',endDate:'2030-01-31',included:'Return flights, Four hotel nights, Meals, Activities, Transfers, Mandatory fees',
 excluded:'Personal shopping',categories:'culture,food,nature',cuisines:'Japanese',listingType:'package',flightsIncluded:'yes',flightType:'round',departureLocations:'sin'
};
for(const [key,value] of Object.entries(data))form.elements[key].value=value;
for(const key of ['culture','food','nature','comfort','intensity'])form.elements['score_'+key].value='8';
for(const key of ['accommodation','transfers','meals','activities','mandatory_fees'])form.elements['coverage_'+key].value='yes';
const app = fs.readFileSync(__dirname + '/../web/agency/app.js','utf8');
const fn = app.slice(app.indexOf('function readPackage('),app.indexOf('function setFormValue('));
const context = {PackageFields,items:[],value:(f,k)=>String(f.elements[k].value||'').trim(),parseList:s=>s.split(/[,\n]/).map(x=>x.trim()).filter(Boolean),form};
vm.createContext(context);vm.runInContext(fn+'; saved=readPackage(form);',context);
// Exercise edit serialization, including supported fields that have no visible editor.
context.items=[{...context.saved,packageId:'p',activityLevel:4}];
form.elements.packageId.value='p';
vm.runInContext('saved=readPackage(form);',context);
if(context.saved.activityLevel!==4)throw Error('Editing lost an existing supported field');
console.log(JSON.stringify(context.saved));
