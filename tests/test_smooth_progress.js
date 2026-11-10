const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
process.chdir(require('node:path').resolve(__dirname, '..'));
class El{
 constructor(tag){this.tag=tag;this.children=[];this.style={};this.dataset={};this.className='';this._text='';this.scrollTop=300;this.scrollHeight=1600;this.clientHeight=500;this.open=false;this.classList={add:x=>{this.className+=' '+x},remove(){},contains:()=>false};}
 append(...xs){for(const x of xs){this.children.push(x);if(typeof x==='object')x.parent=this;}}
 appendChild(x){this.append(x)}
 remove(){this.removed=true;if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
 set textContent(x){this._text=x;this.children=[]} get textContent(){return this._text+this.children.map(c=>typeof c==='object'?c.textContent:c).join('')}
 find(fn){if(fn(this))return this;for(const c of this.children){if(typeof c==='object'){const r=c.find(fn);if(r)return r;}}return null}
 querySelector(s){return this.find(e=>s.startsWith('.')?e.className.split(' ').includes(s.slice(1)):e.tag===s)}
 addEventListener(){} focus(){}
}
const chat=new El('div'),body=new El('body');body.append(chat);chat.id='chat';
const storage=new Map();const context=vm.createContext({document:{createElement:t=>new El(t),getElementById:id=>body.find(e=>e.id===id)||new El('div')},window:{location:{origin:'https://test'}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},TripAuth:{create:()=>({})},TripJobs:{create:()=>({check(){}})},URLSearchParams,TextDecoder,console});
const html=fs.readFileSync('web/index.html','utf8');const code=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1].split('/* ---------- start ---------- */')[0];vm.runInContext(code,context);
context.showChips=()=>{};
storage.set('pending_run',JSON.stringify({requestId:'one',sessionId:'chat',background:true,saved:true}));
vm.runInContext('applyBackgroundStatus({run:{requestId:"one",status:"planning",progress:"Searching flights"}})',context);
const box=body.find(e=>e.id==='runRecovery'), button=box.querySelector('button'), details=chat.querySelector('details');details.open=true;const list=details.querySelector('ol');const count=list.children.length;
chat.scrollTop=200;
for(let i=0;i<4;i++)vm.runInContext('applyBackgroundStatus({run:{requestId:"one",status:"planning",progress:"Searching flights"}})',context);
assert.equal(box,body.find(e=>e.id==='runRecovery'));assert.equal(button,box.querySelector('button'));assert.equal(details.open,true);assert.equal(list.children.length,count);assert.equal(chat.scrollTop,200);assert(chat.querySelector('.spin'));
vm.runInContext('applyBackgroundStatus({run:{requestId:"one",status:"planning",progress:"Creating PDF"}})',context);
assert.equal(list.children.length,count+1);assert.equal(chat.scrollTop,200);assert.equal(details.open,true);
vm.runInContext('applyBackgroundStatus({run:{requestId:"one",status:"completed"}})',context);
assert.equal(body.find(e=>e.id==='runRecovery'),null);assert.equal(chat.querySelector('.spin'),null);assert.equal(chat.scrollTop,200);
console.log('PASS: stable nodes/buttons, spinner stays active, expanded steps stay open, unchanged polls add no steps, scroll preserved through progress/completion');
