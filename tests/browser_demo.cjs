// Run with an installed Playwright via NODE_PATH. Credentials are supplied only in memory.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const url=process.env.GENESIS_BROWSER_URL||'http://127.0.0.1:18769';
 if(process.env.GENESIS_BROWSER_WRITE==='1')assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(url).hostname),'Write checks require an isolated loopback deployment');
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],timings=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const api=[];page.on('response',async r=>{if(r.url().includes('/api/')&&r.status()>=500)errors.push(r.status()+' '+r.url());});
 const measured=async(name,fn)=>{const t=Date.now();await fn();timings.push({name,ms:Date.now()-t});};
 try{
 await page.goto(url);await page.getByLabel('Curator',{exact:true}).fill(process.env.GENESIS_BROWSER_USER||'curator');await page.getByLabel('Password',{exact:true}).fill(process.env.GENESIS_BROWSER_PASSWORD);await page.getByRole('button',{name:'Sign in →'}).click();await page.getByRole('heading',{name:'Discover',exact:true}).waitFor();
 await page.getByRole('heading',{name:'Data inventory'}).waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('.activity-row').length>0);
 const eventButtons=page.locator('.activity-row button');await eventButtons.first().click();await page.getByRole('heading',{name:'Source receipt'}).waitFor();await page.getByRole('button',{name:'Close receipt'}).click();
 await page.getByLabel('Archived activity',{exact:true}).check();await page.waitForTimeout(500);await page.getByLabel('Archived activity',{exact:true}).uncheck();
 const nav=name=>page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name,exact:true});
 await measured('Curation initial',async()=>{await nav('Curation').click();await page.locator('tbody tr').first().waitFor();});
 for(const m of ['DAP-seq','TSS','Bulk ATAC','scATAC','MPRA','Genomes','RNA-seq','ChIP-seq','WGBS']){
  await measured('tab '+m,async()=>{await page.getByRole('group',{name:'Data types'}).getByRole('button',{name:new RegExp('^'+m.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+' ')}).click();await page.waitForFunction(()=>!document.querySelector('.metadata-table')?.getAttribute('aria-busy')||document.querySelector('.metadata-table')?.getAttribute('aria-busy')==='false');});
 }
 await page.getByRole('group',{name:'Data types'}).getByRole('button',{name:/^Bulk ATAC /}).click();
 await measured('study filter',async()=>{await page.getByLabel('Search',{exact:true}).fill('SRP162628');await page.waitForFunction(()=>{const rows=[...document.querySelectorAll('tbody tr')];return rows.length>0&&rows.length<20&&rows.every(r=>r.textContent.includes('SRP162628'));});});
 assert.equal(await page.getByRole('button',{name:'Curate all',exact:true}).count(),0);
 await page.getByLabel('Select page',{exact:true}).check();await page.getByRole('button',{name:'Curate selected studies'}).click();await page.getByRole('region',{name:'Curation selection'}).waitFor();assert.match(await page.getByRole('region',{name:'Curation selection'}).innerText(),/1 studies/);await page.getByRole('region',{name:'Curation selection'}).getByRole('button',{name:'Cancel',exact:true}).click();
 await page.getByRole('button',{name:'History',exact:true}).first().click();await page.getByRole('dialog',{name:'Record history'}).waitFor();await page.getByRole('button',{name:'Review study',exact:true}).click();await page.getByRole('heading',{name:'SRP162628',exact:true}).waitFor();
 assert.equal(await page.locator('.study-sidebar').getByText(/Source identity needed ·/).count(),0);
 await page.getByRole('button',{name:/Check uncertain assay/}).click();await page.getByRole('region',{name:'Current decision'}).waitFor();await page.waitForFunction(()=>document.querySelector('.decision-panel small[role="status"]')?.textContent==='Saved');
 const sourceEvidence=page.locator('.source-evidence').first();
 assert.equal(await sourceEvidence.getAttribute('open'),null);
 const exactQuote=await sourceEvidence.locator('mark').textContent();
 assert.ok(exactQuote.length>360);
 assert.equal(await sourceEvidence.locator('blockquote').isVisible(),false);
 await sourceEvidence.locator('summary').click();
 assert.equal(await sourceEvidence.locator('blockquote').isVisible(),true);
 assert.equal(await sourceEvidence.locator('mark').textContent(),exactQuote);
 await sourceEvidence.locator('summary').click();
 const note=page.getByLabel('Review note',{exact:true});const originalNote=await note.inputValue();
 if(process.env.GENESIS_BROWSER_WRITE==='1'){
  const savedDraft=page.waitForResponse(r=>r.url().includes('/review-tasks/')&&r.url().endsWith('/draft')&&r.request().method()==='POST');
  await note.fill('Isolated browser test draft');assert.equal((await savedDraft).status(),200);await page.waitForFunction(()=>document.querySelector('.decision-panel small[role="status"]')?.textContent==='Saved');
  await page.reload();await nav('Review').click();await page.getByRole('heading',{name:'SRP162628',exact:true}).waitFor();await page.getByRole('button',{name:/Check uncertain assay/}).click();await page.waitForFunction(()=>document.querySelector('textarea[aria-label="Review note"]')?.value==='Isolated browser test draft');
  const restoredDraft=page.waitForResponse(r=>r.url().includes('/review-tasks/')&&r.url().endsWith('/draft')&&r.request().method()==='POST');
  await page.getByLabel('Review note',{exact:true}).fill(originalNote);assert.equal((await restoredDraft).status(),200);await page.waitForFunction(()=>document.querySelector('.decision-panel small[role="status"]')?.textContent==='Saved');
 }
 if(process.env.GENESIS_BROWSER_WRITE==='1'){
  await page.getByRole('button',{name:'Defer',exact:true}).click();await page.getByText('Decision saved. Continue this study’s checklist.').waitFor();
  await page.getByRole('button',{name:/Check uncertain assay/}).click();await page.getByRole('region',{name:'Current decision'}).waitFor();await page.waitForFunction(()=>document.querySelector('.decision-panel small[role="status"]')?.textContent==='Saved');
 }
 assert.equal(await page.getByRole('button',{name:'Sign off study',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:'Record summary · original → prepared values'}).click();await page.locator('.summary-table tbody tr').first().waitFor();await page.getByText('Change history',{exact:true}).click();await page.waitForFunction(()=>[...document.querySelectorAll('details[open] pre')].some(e=>e.textContent.startsWith('[')));
 await page.getByRole('navigation').getByText('Tools',{exact:true}).click();await page.getByRole('button',{name:'Models & skills',exact:true}).click();await page.getByRole('heading',{name:'Local models',exact:true}).waitFor();
 await page.evaluate(()=>window.scrollTo(0,900));
 await measured('Discover warm',async()=>{await nav('Discover').click();await page.locator('.inventory-counts strong').first().waitFor();});
 assert.deepEqual(await page.evaluate(()=>({scrollY,focused:document.activeElement===document.querySelector('main h1')})),{scrollY:0,focused:true});
 for(const name of ['Curation','Review','Discover']){
  await page.evaluate(()=>window.scrollTo(0,900));await nav(name).click();
  assert.deepEqual(await page.evaluate(()=>({scrollY,focused:document.activeElement===document.querySelector('main h1')})),{scrollY:0,focused:true});
 }
 const metrics=await page.evaluate(async()=>{const results=[];for(const path of ['/api/workspace','/api/workspace/events','/api/workspace/rows?modality=Bulk%20ATAC&query=SRP162628','/api/study-review']){const times=[];let bytes=0;for(let i=0;i<5;i++){const t=performance.now(),r=await fetch(path),text=await r.text();if(!r.ok)throw new Error(path+' '+r.status);bytes=text.length;times.push(Math.round(performance.now()-t));}results.push({path,bytes,times});}return results;});api.push(...metrics);
 if(process.env.GENESIS_BROWSER_SCREENSHOT)await page.screenshot({path:process.env.GENESIS_BROWSER_SCREENSHOT,fullPage:true});
 await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.getByRole('heading',{name:'Sign in',exact:true}).waitFor();
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'PASS',url,timings,api,errors,scientific_decisions_submitted:process.env.GENESIS_BROWSER_WRITE==='1'?1:0,draft_roundtrip:process.env.GENESIS_BROWSER_WRITE==='1'},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
