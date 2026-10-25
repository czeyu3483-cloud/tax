'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const appPath = path.join(__dirname, '../web/static/app.js');
const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
const source = fs.readFileSync(appPath, 'utf8').replace(/\ninitialize\(\);\s*$/, '\n');
function harness(storedMode) {
  class Element {
    constructor(tag = 'div') {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.dataset={};this.style={};this.className='';this.textContent='';this.value='';this.disabled=false;this.hidden=false;this.parent=null;}
    append(...children) {for(const child of children){const node=typeof child==='string'?Object.assign(new Element('#text'),{textContent:child}):child; node.parent=this;this.children.push(node);}}
    replaceChildren(...children){this.children.forEach(x=>x.parent=null);this.children=[];this.append(...children);}
    setAttribute(key,value){this.attributes[key]=String(value);}
    getAttribute(key){return this.attributes[key]??null;}
    removeAttribute(key){delete this.attributes[key];}
    addEventListener(event,handler){(this.listeners[event]??=[]).push(handler);}
    dispatchEvent(event){for(const handler of this.listeners[event.type]||[]) handler(event);}
    async click(){if(this.disabled)return;for(const handler of this.listeners.click||[])await handler({type:'click',preventDefault(){}});}
    focus(){}
    querySelectorAll(selector){const result=[];const walk=node=>{for(const child of node.children){if(matches(child,selector))result.push(child);walk(child);}};walk(this);return result;}
    get classList(){return {remove:()=>{},toggle:()=>false};}
  }
  function matches(node,selector){
    return selector.split(',').some(raw=>{
      const part=raw.trim();if(part.startsWith('#'))return node.id===part.slice(1);
      if(part==='.clarification-panel input')return node.tagName==='INPUT' && ancestor(node,'clarification-panel');
      if(part.startsWith('.'))return node.className.split(' ').includes(part.slice(1));
      return node.tagName===part.toUpperCase();
    });
  }
  function ancestor(node,name){while(node.parent){node=node.parent;if(node.className.split(' ').includes(name))return true;}return false;}
  const root=new Element('body'), nodes={};
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)){const node=new Element();node.id=id;nodes[id]=node;root.append(node);}
  for(const id of ['analyze-existing','news-refresh','send-button','new-chat','mic-button','attach-button'])nodes[id].tagName='BUTTON';
  for(const id of ['news-category','news-domain','news-region','news-period'])nodes[id].value='all';
  const workspace=new Element();workspace.className='workspace';root.append(workspace);
  const document={
    hidden:false, createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
    getElementById:id=>nodes[id]||root.querySelectorAll('#'+id)[0]||null,
    querySelectorAll:selector=>root.querySelectorAll(selector),
    querySelector:selector=>root.querySelectorAll(selector)[0]||null,addEventListener(){},
  };
  const calls=[];
  let responder=async(url,options)=>({ok:true,json:async()=>url==='/api/conversations'?{conversations:[]}:{items:[],status:{running:false}}});
  const context=vm.createContext({
    document, localStorage:{getItem:key=>key==='xiaochi-answer-mode'?storedMode:null,setItem(){},removeItem(){}},
    fetch:async(url,options={})=>{calls.push({url,options});return responder(url,options);},
    navigator:{clipboard:{writeText:async()=>{}}},voiceButton:()=>new Element('button'),
    stopVoice(){},toggleMicrophone(){},performance:{now:()=>0},requestAnimationFrame:callback=>callback(),
    crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000000'},setInterval:()=>1,clearInterval(){},
    setTimeout:()=>1,clearTimeout(){},URL,URLSearchParams,Intl,Date,console,
    Event:class {constructor(type){this.type=type;}}
  });
  vm.runInContext(source,context);
  return {nodes,root,calls,run:code=>vm.runInContext(code,context),respond:fn=>responder=fn};
}
function seed(h) {
  h.run("state.id='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';state.messages=[{role:'user',content:'案例'},{role:'assistant',content:'请补充',mode:'interactive',clarification_questions:[{id:'r1q1',prompt:'主体身份？',options:['企业','个人'],allow_free_text:true},{id:'r1q2',prompt:'资金用途？',options:['投资','贸易'],allow_free_text:true}]}];renderMessages();");
}
const answer = {conversation_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', answer:'分析', mode:'interactive',clarification_questions:[],citations:[],case_state:{tendency:'undecided',tendency_provided:false,tendency_note:''}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let h=harness();
  assert.equal(h.nodes['answer-mode'].value,'interactive');
  assert.equal(harness('rough').nodes['answer-mode'].value,'rough');
  assert.equal(harness('precise').nodes['answer-mode'].value,'precise');
  assert.equal(harness('corrupt').nodes['answer-mode'].value,'interactive');

  assert.deepEqual(JSON.parse(h.run("JSON.stringify(policyDateRange('week', new Date('2026-10-25T01:00:00Z')))")), {date_from:'2026-10-19',date_to:'2026-10-25'});
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(policyDateRange('month', new Date('2026-10-31T16:30:00Z')))")), {date_from:'2026-11-01',date_to:'2026-11-01'});
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(policyDateRange('week', new Date('2027-01-01T01:00:00Z')))")), {date_from:'2026-12-28',date_to:'2027-01-01'});
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(policyDateRange('all'))")), {});

  seed(h);
  assert.equal(h.root.querySelectorAll('fieldset').length,2);
  const choice=h.root.querySelectorAll('.clarification-option')[0];
  await choice.click();
  assert.equal(h.calls.length,0,'choice must not send a request');
  assert.equal(h.run("state.answerDrafts.r1q1.selected_option"),'企业');
  h.nodes.question.value='补充文字';
  h.respond(async(url,options)=>({ok:true,json:async()=>url.endsWith('/messages')?answer:url==='/api/conversations'?{conversations:[]}:{items:[],status:{running:false}}}));
  await h.run('submitConsultation()');
  let payload=JSON.parse(h.calls.find(x=>x.url.endsWith('/messages')).options.body);
  assert.equal(payload.answers[0].question_id,'r1q1');
  assert.equal(payload.answers[0].selected_option,'企业');
  assert.equal(payload.message,'补充文字');
  assert.equal(payload.mode,'interactive');
  assert.ok(!Object.hasOwn(payload,'tendency'),'default undecided must not be submitted');
  assert.equal(h.root.querySelectorAll('fieldset').length,0,'previous questions become read-only');

  h=harness();seed(h);
  await h.root.querySelectorAll('.clarification-option')[1].click();
  h.nodes.question.value='失败后保留';
  h.respond(async(url)=>{if(url.endsWith('/messages')) throw new Error('offline'); return {ok:true,json:async()=>({items:[],status:{running:false}})};});
  await h.run('submitConsultation()');
  assert.equal(h.nodes.question.value,'失败后保留');
  assert.equal(h.run("state.answerDrafts.r1q1.selected_option"),'个人');
  assert.equal(h.root.querySelectorAll('fieldset').length,2);
  assert.equal(h.run('state.busy'),false);

  h=harness();seed(h);
  await h.root.querySelectorAll('.clarification-skip')[0].click();
  assert.equal(h.calls.length,0);
  h.respond(async(url)=>({ok:true,json:async()=>url.endsWith('/messages')?answer:url==='/api/conversations'?{conversations:[]}:{items:[],status:{running:false}}}));
  await h.run("submitConsultation('analyze_existing')");
  payload=JSON.parse(h.calls.find(x=>x.url.endsWith('/messages')).options.body);
  assert.equal(payload.message,'');assert.equal(payload.action,'analyze_existing');
  assert.equal(payload.answers[0].skipped,true);assert.equal(payload.answers[0].text,'');

  h=harness();seed(h);
  await h.root.querySelectorAll('.clarification-option')[0].click();
  h.nodes['answer-mode'].value='rough';h.nodes['answer-mode'].dispatchEvent({type:'change'});
  assert.equal(h.root.querySelectorAll('fieldset').length,0);
  assert.equal(h.run('submittedAnswers().length'),0);
  for(const url of ['javascript:alert(1)','file:///tmp/x','http://localhost/','http://127.0.0.1','http://2130706433/','https://x.local/','https://user:secret@example.com','https://example.com:22/'])assert.equal(h.run('safeHref('+JSON.stringify(url)+')'),'');
  assert.equal(h.run("safeHref('https://www.safe.gov.cn/a')"),'https://www.safe.gov.cn/a');

  h=harness();seed(h);
  let release;
  h.respond((url)=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({items:[{title:'旧对话动态',url:'https://www.safe.gov.cn/a'}],status:{running:false}})});}));
  h.run('startNewsPolling()');await tick();
  h.run("stopNewsPolling();state.id='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';renderNews(null);");
  const fresh=h.nodes['news-status'].textContent;release();await tick();await tick();
  assert.equal(h.nodes['news-count'].textContent,0);
  assert.equal(h.nodes['news-status'].textContent,fresh,'old conversation response must not render');

  h=harness();seed(h);
  let finish;
  h.respond((url)=>url.endsWith('/messages')?new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>answer});}):Promise.resolve({ok:true,json:async()=>url==='/api/conversations'?{conversations:[]}:{items:[],status:{running:false}}}));
  h.nodes.question.value='生成中';
  const first=h.run('submitConsultation()');await tick();
  await h.run('submitConsultation()');
  assert.equal(h.calls.filter(x=>x.url.endsWith('/messages')).length,1);
  assert.equal(h.nodes['analyze-existing'].disabled,true);
  finish();await first;
  h=harness();seed(h);
  h.run("renderNews({items:[{title:'公开动态',url:'https://www.safe.gov.cn/future',category:'upcoming',effective_at:'2027-01-01',effective_evidence_excerpt:'自2027年1月1日起施行'}],status:{running:false}})");
  await h.root.querySelectorAll('.news-review')[0].click();
  assert.equal(h.calls.length,0,'news review only fills the input');
  assert.ok(h.nodes.question.value.includes('https://www.safe.gov.cn/future'));
  assert.equal(h.nodes['answer-mode'].value,'interactive','news review does not silently change mode');
  assert.ok(h.root.querySelectorAll('.news-date-evidence').length);
  console.log('Frontend interaction checks passed: modes, drafts, submit, restore, skip, analyze, historical controls, links, stale news, duplicate requests.');
})().catch(error=>{console.error(error);process.exitCode=1;});
