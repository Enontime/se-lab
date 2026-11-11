/** Deterministic async response-gating tests without browser or Flask.
 * Tests four reported bugs and stale-401, while real-browser QA remains required.
 * node qa_tests/frontend_vm_v042.cjs
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'app/static/js/site.js'), 'utf8');
const delay = () => new Promise(resolve => setImmediate(resolve));
const pending = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };

function harness({token = '', start = '#/mine', holdOld = false, stale401 = false, invalidJSON = false} = {}) {
  const nodes = Object.fromEntries(['view', 'top-nav', 'account-bar', 'toast'].map(k => [k, {innerHTML: '', textContent: '', className: ''}]));
  const events = {document:{}, window:{}};
  const storage = new Map(token ? [['campus_market_token', token]] : []);
  let hash = start;
  const calls = [];
  const oldStarted = pending(), releaseOld = pending();
  let firstOld = true;
  const identities = {'seller-token':{id:1,username:'qa_seller',account_type:'NORMAL'}, 'other-token':{id:2,username:'qa_intruder',account_type:'NORMAL'}, 'admin-token':{id:3,username:'qa_admin',account_type:'ADMIN'}};
  const js = {
    console,
    Promise, URLSearchParams, BigInt, Map, Math, Number, String, Object, JSON,
    sessionStorage: {
      getItem:k=>storage.get(k)||null,
      setItem:(k,v)=>storage.set(k,v),
      removeItem:k=>storage.delete(k),
    },
    location: {
      get hash(){return hash},
      set hash(h){hash=h;queueMicrotask(()=>events.window.hashchange?.());}
    },
    document: {
      getElementById:k=>nodes[k],
      addEventListener:(k,fn)=>{events.document[k]=fn},
      querySelectorAll:()=>[],
    },
    window: {addEventListener:(k,fn)=>{events.window[k]=fn},scrollTo(){}},
    setTimeout:()=>1, clearTimeout:()=>{},
    crypto: {randomUUID:()=> 'unique-req-id'},
    confirm:()=>true,prompt:()=>'',
    FormData:class { constructor(form){ this.values = Object.entries(form.values||{}); } [Symbol.iterator](){return this.values[Symbol.iterator]();}},
    async fetch(u,o={}) {
      const token=(o.headers?.Authorization || '').replace('Bearer ','');
      const endpoint = u.split('?')[0];
      calls.push({endpoint,url:u,token,opts:o});
      const respond = (data,status=200)=>({ok:status<400,status,async json(){if(data==='INVALID_JSON')throw new SyntaxError('invalid JSON');return data;}});
      if (endpoint==='/api/auth/me') return respond(identities[token] || {error:{message:'unauthorized'}},identities[token]?200:401);
      if (endpoint==='/api/auth/logout') return respond({ok:true});
      if (endpoint==='/api/auth/login'){
        const username=JSON.parse(o.body).username;
        const newToken=username==='qa_intruder'?'other-token':username==='qa_admin'?'admin-token':'seller-token';
        return respond({token:newToken,user:identities[newToken]});
      }
      if (endpoint==='/api/my/products'){
        if (token==='seller-token' && firstOld && holdOld){
          firstOld=false;
          oldStarted.resolve();
          await releaseOld.promise;
          if (stale401) return respond({error:{message:'expired'}},401);
        }
        return respond({items:token==='seller-token'?[{id:42,status:'DRAFT',title:'SECRET_SELLER_DRAFT',price_cents:3000}]:[]});
      }
      if (endpoint==='/api/my/dashboard')return respond({products:{total:token==='seller-token'?1:0}});
      if (endpoint==='/api/products')return respond(invalidJSON?'INVALID_JSON':{items:[]});
      if (endpoint==='/api/products/facets')return respond({categories:[],total_on_sale:0,available:0});
      if (endpoint==='/api/admin/logs')return respond({items:[]});
      return respond({error:{message:'unimplemented mock '+endpoint}},404);
    },
  };
  vm.runInNewContext(source,js,{filename:'site.js'});
  async function settle(){for(let i=0;i<8;i++)await delay();}
  async function submit(id, values){
    const button={disabled:false};
    const form={dataset:{form:id},values,closest(){return this},querySelector(){return button}};
    events.document.submit({target:form,submitter:button,preventDefault(){}});
    await settle();
  }
  async function click(act, extra={}){
    const trigger={dataset:{act,...extra},disabled:false};
    events.document.click({target:{closest(){return trigger}},preventDefault(){}});
    await settle();
  }
  return {nodes,storage,calls,js,events,oldStarted,releaseOld,settle,submit,click};
}

async function run(name, test){
 try{await test();console.log('PASS',name);}
 catch(e){console.error('FAIL',name,e.stack);process.exitCode=1;}
}
(async()=>{
  for (const stale401 of [false,true]) await run(stale401?'P0 stale 401 must not log out new account':'P0 old account response must not render',async()=>{
    const h=harness({token:'seller-token',holdOld:true,stale401});
    await h.oldStarted.promise;
    await h.click('logout');
    await h.submit('auth',{username:'qa_intruder',password:'QaPassword2026!'});
    h.js.location.hash='#/mine'; await h.settle();
    assert.match(h.nodes['account-bar'].innerHTML,/qa_intruder/);
    assert.doesNotMatch(h.nodes.view.innerHTML,/SECRET_SELLER_DRAFT/);
    h.releaseOld.resolve();await h.settle();
    assert.doesNotMatch(h.nodes.view.innerHTML,/SECRET_SELLER_DRAFT/);
    assert.match(h.nodes['account-bar'].innerHTML,/qa_intruder/);
    assert.equal(h.storage.get('campus_market_token'),'other-token');
  });
  await run('P2 filter stays in yuan',async()=>{
    const h=harness({start:'#/browse'}); await h.settle();
    await h.submit('catalog',{keyword:'',category:'',min_price:'30',max_price:'30',availability:''});
    assert.match(h.nodes.view.innerHTML,/name="min_price" value="30"/);
    assert.match(h.nodes.view.innerHTML,/name="max_price" value="30"/);
    await h.submit('catalog',{keyword:'',category:'',min_price:'30',max_price:'30',availability:''});
    const qs=h.calls.filter(x=>x.endpoint==='/api/products').slice(-2).map(x=>new URLSearchParams(x.url.split('?')[1]));
    assert.equal(qs.length,2);for(const p of qs){assert.equal(p.get('min_price'),'3000');assert.equal(p.get('max_price'),'3000');}
  });
  await run('P2 price boundaries 0 and 0.01 yuan',async()=>{
    const h=harness({start:'#/browse'});await h.settle();
    for(const [input,expectedCents,expectedText] of [['0','0','0'],['0.01','1','0.01'],['30.00','3000','30']]){
      await h.submit('catalog',{keyword:'',category:'',min_price:input,max_price:input,availability:''});
      assert(h.nodes.view.innerHTML.includes(`name="min_price" value="${expectedText}"`));
      const last=h.calls.filter(x=>x.endpoint==='/api/products').at(-1);
      const params=new URLSearchParams(last.url.split('?')[1]);
      assert.equal(params.get('min_price'),expectedCents);
    }
  });
  await run('P2 malformed JSON is error',async()=>{
    const h=harness({start:'#/browse',invalidJSON:true}); await h.settle();
    assert.match(h.nodes.view.innerHTML,/服务器响应格式错误/);
    assert.doesNotMatch(h.nodes.view.innerHTML,/暂无符合条件的商品/);
  });
  await run('P2 logs fields associated with labels',async()=>{
    for(const [id,name] of [['log-action','action'],['log-actor','actor_id'],['log-start','start'],['log-end','end']]){
      assert(source.includes(`for="${id}"`),id);
      assert(source.includes(`id="${id}"`),id);
    }
  });
})();
