import { useEffect, useRef, useState } from 'react';

export type API = <T>(path: string, data?: unknown) => Promise<T>;
type Citation = {source_version: string; locator: string; quote: string};
type Field = {field: string; value: string | number | null; status: string; rationale: string; evidence: Citation[]};
type Source = {version: string; data: {snapshot: string; location: string}};
type RecordRow = {preparation_provenance?:{coverage?:{available_passages?:number;selected_passages?:number;omitted_passages?:number}};id: string; name: string; version: string; classification: string; fields: Field[]; sources: Source[]; reviews: Record<string,{action:string}>};
type Target = {subject: string; name: string; version: string; field: Field | null; recommendation: Field | null; review?: {reason: string; actor: string}};
export type Task = {id: string; study: string; version: string; title: string; reason: string; kind: string; field: string; status: string; targets: Target[]};
export type Study = {id: string; name: string; version: string; status: string; member_count: number; pending: number; completed_tasks: number; approval_current: boolean; approval: string | null; records: RecordRow[]; tasks: Task[]; preparation: {status: string; mode?: string; job?: string; completed: Record<string,string>; failures: Record<string,string>; retrieval: {url: string; status: string; reason?: string}[]}};
type Progress = {study:string; status:string; phase:string; total:number; prepared:number; failed:number; batches_done:number; active_rows:number; elapsed_seconds:number; eta_seconds:number|null; updated:string; budget:Record<string,unknown>|null};
type Provider = {id: string; model: string; external: boolean; mode: string; allowed_classifications: string[]};
type Draft = {reason: string; remember?: boolean; evidence?: {text:string;label:string;subject:string}; corrections: Record<string, {value: string; citation: string}>};
const nice = (value: string) => value.replaceAll('_', ' ');
const statusLabel: Record<string,string> = {not_prepared: 'Ready to prepare', preparing: 'AI preparing', needs_decisions: 'Needs decisions', ready: 'Ready for sign-off', signed: 'Signed off'};

function excerpts(record?: RecordRow): (Citation & {location: string})[] {
    const result: (Citation & {location: string})[] = [];
    for (const source of record?.sources || []) {
        const add = (locator: string, quote: string) => { if (quote.trim() && result.length < 600) result.push({source_version: source.version, locator, quote, location: source.data.location}); };
        try {
            const raw: unknown = JSON.parse(source.data.snapshot);
            if (raw && typeof raw === 'object' && !Array.isArray(raw)) Object.entries(raw).forEach(([key,value]) => add('/' + key.replaceAll('~','~0').replaceAll('/','~1'), typeof value === 'string' ? value : JSON.stringify(value)));
            else source.data.snapshot.split('\n').forEach((line,index) => add(`line:${index+1}`,line));
        } catch { source.data.snapshot.split('\n').forEach((line,index) => add(`line:${index+1}`,line)); }
    }
    return result;
}

export function StudyWorkspace({api, provider, providers, setProvider, onDirty}: {onDirty:(dirty:boolean)=>void; api: API; provider: string; providers: Provider[]; setProvider: (id: string) => void}) {
    const [items,setItems] = useState<Study[]>([]), [cursor,setCursor] = useState<string|null>(null), [total,setTotal] = useState(0);
    const [selected,setSelected] = useState(''), [study,setStudy] = useState<Study|null>(null), [taskId,setTaskId] = useState('');
    const [error,setError] = useState(''), [notice,setNotice] = useState(''), [busy,setBusy] = useState(false), [adding,setAdding] = useState(false), [dirty,setDirty] = useState(false);
    const [tab,setTab] = useState('open'), [importJob,setImportJob] = useState(''), [samplePage,setSamplePage] = useState(0);
    const [progress,setProgress] = useState<Progress|null>(null);
    const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
    const [readyStudy,setReadyStudy] = useState('');
    const [history,setHistory] = useState<unknown[]|null>(null);
    useEffect(()=>{onDirty(dirty);return ()=>onDirty(false);},[dirty]);
    const moreLoaded = useRef(false);
    const selectedRef = useRef(selected); selectedRef.current = selected;
    const taskRef = useRef(taskId); taskRef.current = taskId;
    const refresh = async () => {
        const page = await api<{items:Study[];next_cursor:string|null;total:number}>('/studies');
        setItems(old => [...page.items, ...old.filter(s => !page.items.some(x=>x.id === s.id))]); if(!moreLoaded.current)setCursor(page.next_cursor); setTotal(page.total);
        if (selectedRef.current) {
            const current = await api<Study>('/studies/'+selectedRef.current);
            if (current.id === selectedRef.current) setStudy(current);
        }
    };
    useEffect(() => {
        let live = true;
        void api<{study?:string;task?:string;import_job?:string}>('/review-tasks/workspace/draft').then(saved => {
            if(live){if(saved.study){setSelected(saved.study);setTaskId(saved.task||'');}if(saved.import_job)setImportJob(saved.import_job);}
        }).catch(e=>setError(String(e)));
        void refresh().catch(e=>setError(String(e)));
        const timer = setInterval(()=>{
            if(selectedRef.current) void api<Progress>('/studies/'+selectedRef.current+'/progress').then(p=>{
                if(p.study!==selectedRef.current)return;
                setProgress(p);
            }).catch(e=>setError(String(e)));
        },3000);
        return () => {live=false;clearInterval(timer);};
    },[]);
    useEffect(() => {
        if (!selected) return;
        setStudy(null); setProgress(null); setHistory(null); setSamplePage(0);
        void api<Study>('/studies/'+selected).then(s=>{if(selectedRef.current===s.id)setStudy(s);}).catch(e=>setError(String(e)));
    },[selected]);
    useEffect(() => {
        if (selected||importJob) void api('/review-tasks/workspace/draft',{study:selected,task:taskId,import_job:importJob}).catch(e=>setError(String(e)));
    },[selected,taskId,importJob]);
    useEffect(() => {
        if (!importJob) return;
        let live=true;
        const poll = async () => {
            const j = await api<{status:string;error:string|null;result?:{study:string}}>('/jobs/'+importJob);
            if (!live) return;
            if(j.result?.study && !selectedRef.current && !dirtyRef.current) setSelected(j.result.study);
            if (j.status==='SUCCEEDED' && j.result) {
                if(!dirtyRef.current)setSelected(j.result.study);else setReadyStudy(j.result.study);
                setImportJob('');setNotice('Study prepared. Your decisions are ready.');
                if(!dirtyRef.current)await refresh();
            }
            else if (['FAILED','CANCELLED','INTERRUPTED'].includes(j.status)) {setError(j.error || 'Import interrupted. Retrying reuses cached metadata.');setImportJob('');await refresh();}
        };
        void poll().catch(e=>setError(String(e)));
        const timer=setInterval(()=>void poll().catch(e=>setError(String(e))),2000);
        return ()=>{live=false;clearInterval(timer);};
    },[importJob]);
    async function work(fn:()=>Promise<void>) {setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
    const visible = study?.tasks.filter(t=>t.status===tab) || [];
    const task = visible.find(t=>t.id===taskId) || visible[0];
    const running = progress ? ['RUNNING','QUEUED'].includes(progress.status) : study?.status==='preparing';
    useEffect(()=>{
        if(progress?.status==='COMPLETED' && study?.status==='preparing' && !dirty) void refresh().catch(e=>setError(String(e)));
    },[progress?.status,dirty]);
    async function resolved(updated: Study) {
        setStudy(updated);setTaskId('');setDirty(false);setNotice('Decision saved. Your next task is ready.');await refresh();
    }
    async function prepare() {
        if(!study)return;
        await api('/studies/'+study.id+'/prepare',{version:study.version,provider});setNotice('Preparation queued. You can leave and return.');await refresh();
    }
    return <section className="study-workspace">
        <div className="study-intro"><div><p>AI prepares the metadata. You resolve the exceptions and sign off once.</p><small>Signed metadata and scientific QC are tracked separately.</small></div><button disabled={dirty} onClick={()=>setAdding(!adding)}>＋ Add studies</button></div>
        {readyStudy && <button disabled={dirty} onClick={()=>{setSelected(readyStudy);setReadyStudy('');void refresh();}}>Open prepared study</button>}
        {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" className="notice">{notice}</p>}
        {importJob && <div className="preparation"><strong>{progress ? (progress.phase==='followup' ? 'Checking uncertain fields…' : 'Reviewing your metadata table…') : 'Retrieving metadata…'}</strong><span>{progress ? `${progress.prepared} of ${progress.total} samples prepared` : 'Samples and linked publications are cached as they arrive.'}</span><button className="secondary" onClick={()=>void work(async()=>{await api('/jobs/'+importJob+'/cancel',{});})}>Cancel import</button></div>}
        {adding && <AddStudy api={api} provider={provider} onAdded={async result=>{setAdding(false);if(result.job){setImportJob(result.job);setSelected('');setProgress(null);}if(result.studies?.[0])setSelected(result.studies[0]);await refresh();}}/>}
        <details className="advanced"><summary>AI connection</summary><label>Provider<select value={provider} onChange={e=>setProvider(e.target.value)}><option value="">Source-only preparation</option>{providers.filter(p=>p.mode!=='disabled').map(p=><option key={p.id} value={p.id}>{p.id} · {p.model}{p.external?' · external API':''}</option>)}</select></label><p>Hosted providers require an explicit selection and permitted data classification. Skills are selected automatically for the assay.</p></details>
        <div className="study-layout"><aside className="panel study-sidebar"><div className="section-title"><h2>Studies</h2><span>{total}</span></div>
            {!items.length && <p>Add an accession or metadata table to begin.</p>}
            {items.map(s=><button disabled={dirty} className={'study-link '+(selected===s.id?'selected':'')} key={s.id} onClick={()=>{setSelected(s.id);setTaskId('');setTab('open');}}><strong>{s.name}</strong><span>{statusLabel[s.status]}</span><small>{s.member_count} records · {s.pending} decisions</small></button>)}
            {cursor && <button className="secondary" onClick={()=>void work(async()=>{moreLoaded.current=true;const p=await api<{items:Study[];next_cursor:string|null}>('/studies?cursor='+cursor);setItems(old=>[...old,...p.items.filter(s=>!old.some(x=>x.id===s.id))]);setCursor(p.next_cursor);})}>More studies</button>}
        </aside><div className="study-body">{!study ? <div className="panel empty"><h2>{selected?'Opening your study…':'Your next decision starts here'}</h2><p>Choose a study to see its preparation progress and review tasks.</p></div> : <>
            <section className="panel study-progress"><div className="section-title"><div><p className="eyebrow">{statusLabel[study.status]}</p><h2>{study.name}</h2></div><span>{study.member_count} records</span></div>
                <div className="progress-line"><strong>{study.pending} decisions remaining</strong><span>{study.completed_tasks} completed</span></div>
                <progress aria-label="Review progress" value={study.completed_tasks} max={Math.max(1,study.completed_tasks+study.pending)}/>
                {running ? <div className="preparation"><div role="status"><strong>{progress?.phase==='followup'?'Checking uncertain fields':'AI reviewing table'}</strong><p>{progress?.prepared ?? Object.keys(study.preparation.completed).length} / {progress?.total ?? study.member_count} samples prepared · {progress?.failed ?? Object.keys(study.preparation.failures).length} failures</p>{progress && <><p>{progress.active_rows===progress.total?'Whole table in one request':progress.active_rows?`Current batch: ${progress.active_rows} samples`:`${progress.batches_done} batches completed`} · {Math.floor(progress.elapsed_seconds/60)}m elapsed · {progress.eta_seconds===null?'Estimating remaining time…':`About ${Math.ceil(progress.eta_seconds/60)}m remaining`}</p><details><summary>Processing details</summary><pre>{JSON.stringify(progress.budget,null,2)}</pre></details></>}</div>{study.preparation.job && <button className="secondary" onClick={()=>void work(async()=>{await api('/jobs/'+study.preparation.job+'/cancel',{});})}>Stop preparation</button>}</div> : study.preparation.status!=='COMPLETED' && <button disabled={busy} onClick={()=>void work(prepare)}>{study.preparation.status==='NOT_STARTED'?'Prepare study':'Resume preparation'}</button>}
                {study.preparation.mode==='source-only' && <p className="notice">Source-only preparation: no model was used. Select an AI connection and prepare again to request suggestions.</p>}
            </section>
            <div className="inbox-toolbar"><div role="tablist" aria-label="Decision status">{[['open','To do'],['waiting','Deferred'],['done','Completed']].map(([key,label])=><button role="tab" aria-selected={tab===key} disabled={dirty} className={tab===key?'current':''} key={key} onClick={()=>{setTab(key);setTaskId('');}}>{label} <b>{study.tasks.filter(t=>t.status===key).length}</b></button>)}</div><span>{dirty?'Saving your draft…':'Progress saved'}</span></div>
            <div className="decision-layout"><nav className="decision-list" aria-label="Study decisions">{visible.map(t=><button key={t.id} disabled={dirty} className={task?.id===t.id?'selected':''} onClick={()=>setTaskId(t.id)}><strong>{t.title}</strong><small>{t.targets.length} affected records</small></button>)}</nav>
            {task ? <Decision key={task.id+':'+task.version} api={api} study={study} task={task} provider={provider} disabled={!!running} onDirty={setDirty} onDone={resolved} refresh={refresh}/> : <section className="panel empty"><h3>{study.pending?'No tasks in this list.':'Your exception list is clear.'}</h3><p>{study.pending?'Deferred decisions still need a resolution before sign-off.':running?'AI is still preparing the study.':study.approval_current?'Study signed off. Your exports are ready below.':'Review the prepared summary below and sign off once.'}</p></section>}</div>
            {study.records.some(r=>(r.preparation_provenance?.coverage?.omitted_passages||0)>0)&&<details className="panel"><summary>AI used selected evidence windows — inspect coverage</summary><p>Original sources are retained. A bounded evidence pass is not an exhaustive source review.</p>{study.records.filter(r=>(r.preparation_provenance?.coverage?.omitted_passages||0)>0).map(r=><p key={r.id}>{r.name}: {r.preparation_provenance?.coverage?.selected_passages} of {r.preparation_provenance?.coverage?.available_passages} passages selected in the latest pass.</p>)}</details>}
            <section className="panel study-summary"><div className="section-title"><div><p className="eyebrow">FINAL STUDY SUMMARY</p><h2>{study.approval_current?'Signed metadata':'Prepared metadata'}</h2></div><span>{study.records.reduce((n,r)=>n+r.fields.filter(f=>f.status==='known'&&(!r.reviews[f.field]||r.reviews[f.field].action==='approve')).length,0)} supported suggestions</span></div>
                <p>Signing accepts supported routine suggestions and your reviewed decisions. Missing and rejected values remain excluded. This does not establish processing success or scientific QC.</p>
                <details><summary>Inspect samples, suggestions and unknowns ({study.records.length} records)</summary><div className="summary-table"><table><thead><tr><th>Sample</th><th>Prepared values</th><th>Unknown / unresolved</th></tr></thead><tbody>{study.records.slice(samplePage*25,(samplePage+1)*25).map(r=><tr key={r.id}><td>{r.name}</td><td>{r.fields.filter(f=>f.status==='known'&&(!r.reviews[f.field]||r.reviews[f.field].action==='approve')).map(f=><div key={f.field}><strong>{nice(f.field)}:</strong> {String(f.value)}</div>)}</td><td>{r.fields.filter(f=>['unknown','conflicting'].includes(f.status)||(r.reviews[f.field]&&r.reviews[f.field].action!=='approve')).map(f=>nice(f.field)).join(', ')}</td></tr>)}</tbody></table></div><div className="task-actions"><button className="secondary" disabled={!samplePage} onClick={()=>setSamplePage(p=>p-1)}>Previous samples</button><span>Page {samplePage+1}</span><button className="secondary" disabled={(samplePage+1)*25>=study.records.length} onClick={()=>setSamplePage(p=>p+1)}>Next samples</button></div></details>
                <details><summary>Evidence retrieval and preparation report</summary><p>{study.preparation.mode==='ai'?'AI-assisted':'Source-only'} · {study.preparation.status}</p>{study.preparation.retrieval.map((r,i)=><p key={i}>{r.status}: {r.url} {r.reason}</p>)}{Object.entries(study.preparation.failures).map(([id,message])=><p key={id} className="error-text">{study.records.find(r=>r.id===id)?.name}: {message}</p>)}</details>
                {!study.approval_current ? <button disabled={busy||dirty||study.status!=='ready'} onClick={()=>void work(async()=>{await api('/studies/'+study.id+'/approve',{version:study.version});setNotice('Study signed off. Your versioned exports are ready.');await refresh();})}>Sign off this study</button> : <div className="release-actions"><strong>Signed off · exports ready</strong>{['json','csv','tsv'].map(format=><a key={format} href={'/api/studies/'+study.id+'/export?format='+format}>{format.toUpperCase()}{format==='json'?' + provenance':''}</a>)}</div>}
                <details><summary onClick={()=>void api<unknown[]>('/studies/'+study.id+'/history').then(setHistory).catch(e=>setError(String(e)))}>Study history</summary><pre>{history?JSON.stringify(history,null,2):'Loading…'}</pre></details>
                {!running && <details><summary>Re-analysis</summary><p>Re-analysis preserves human decisions but requires a new study sign-off.</p><button className="secondary" disabled={busy||dirty} onClick={()=>void work(async()=>{await api('/studies/'+study.id+'/prepare',{version:study.version,provider,restart:true});await refresh();})}>Prepare again with selected model</button></details>}
            </section>
        </>}</div></div>
    </section>;
}

function AddStudy({api,provider,onAdded}: {api: API;provider:string;onAdded:(result:{job?:string;studies?:string[]})=>Promise<void>}) {
    const [accession,setAccession]=useState(''), [text,setText]=useState(''), [filename,setFilename]=useState(''), [namespace,setNamespace]=useState(''), [id,setId]=useState(''), [columns,setColumns]=useState<string[]>([]), [count,setCount]=useState(0), [classification,setClassification]=useState('local-only'), [busy,setBusy]=useState(false), [error,setError]=useState('');
    async function submit(body:unknown) {setBusy(true);setError('');try{await onAdded(await api('/studies',body));}catch(e){setError(String(e));}finally{setBusy(false);}}
    return <section className="panel add-study"><h2>Add a study</h2>{error&&<p role="alert" className="error">{error}</p>}<form onSubmit={e=>{e.preventDefault();void submit({accession,provider});}}><label>Public study or sample accession<input value={accession} onChange={e=>setAccession(e.target.value)} placeholder="GSE… or PRJNA…" required/></label><button disabled={busy}>Retrieve and prepare</button></form><div className="import-divider">or upload a metadata table</div><label>CSV, TSV or JSON · up to 2 MiB per file<input type="file" accept=".csv,.tsv,.json" onChange={e=>{const file=e.target.files?.[0];if(!file)return;void(async()=>{try{if(file.size>2*1024*1024)throw new Error('Split files larger than 2 MiB.');const value=await file.text();const preview=await api<{rows:unknown[];columns:string[]}>('/imports/preview',{text:value,filename:file.name});setText(value);setFilename(file.name);setNamespace(file.name.replace(/\.[^.]+$/,''));setColumns(preview.columns);setCount(preview.rows.length);setId(preview.columns.find(c=>['id','accession','sample_id','run_accession'].includes(c))||preview.columns[0]);}catch(err){setError(String(err));}})();}}/></label>{text&&<form onSubmit={e=>{e.preventDefault();void submit({text,filename,namespace,id_column:id,classification,provider});}}><p>{count} records ready to import. A study_accession column groups separate studies automatically.</p><label>Study/import name<input required value={namespace} onChange={e=>setNamespace(e.target.value)}/></label><label>Unique record identifier<select value={id} onChange={e=>setId(e.target.value)}>{columns.map(c=><option key={c}>{c}</option>)}</select></label><label>Sharing classification<select value={classification} onChange={e=>setClassification(e.target.value)}><option value="local-only">Local only</option><option value="public">Public metadata</option><option value="internal">Internal</option></select></label><button disabled={busy}>Import and prepare</button></form>}</section>;
}

export function Decision({api,study,task,provider,disabled,onDirty,onDone,refresh}: {api:API;study:Study;task:Task;provider:string;disabled:boolean;onDirty:(value:boolean)=>void;onDone:(study:Study)=>Promise<void>;refresh:()=>Promise<void>}) {
    const [draft,setDraft]=useState<Draft>({reason:'',corrections:{}}), [loaded,setLoaded]=useState(false), [saving,setSaving]=useState('Saved'), [error,setError]=useState(''), [busy,setBusy]=useState(false), [editing,setEditing]=useState(false);
    const [records,setRecords]=useState(study.records), [expanded,setExpanded]=useState(false);
    const evidenceTarget=draft.evidence?.subject||task.targets[0]?.subject||'';
    const evidenceLabel=draft.evidence?.label||'', newEvidence=draft.evidence?.text||'';
    const updateEvidence=(key:'text'|'label'|'subject',value:string)=>setDraft(d=>({...d,evidence:{text:newEvidence,label:evidenceLabel,subject:evidenceTarget,...d.evidence,[key]:value}}));
    const latest=useRef(draft);latest.current=draft;
    const saved=useRef('');
    useEffect(()=>{
        let live=true;
        void api<Draft & {version?:string}>('/review-tasks/'+task.id+'/draft').then(d=>{if(!live)return;const restored={reason:d.reason||'',corrections:d.version===task.version?d.corrections||{}:{},evidence:d.evidence,remember:d.remember||false};setDraft(restored);setEditing(Object.keys(restored.corrections).length>0);saved.current=JSON.stringify(restored);setLoaded(true);if(d.version&&d.version!==task.version)setError('Evidence changed since your saved draft. Your note is retained; check the updated samples.');}).catch(e=>setError(String(e)));
        return ()=>{live=false;onDirty(false);};
    },[]);
    useEffect(()=>{
        if(!loaded||JSON.stringify(draft)===saved.current)return;
        setSaving('Saving…');onDirty(true);
        const timer=setTimeout(()=>{
            const serial=JSON.stringify(draft);
            void api('/review-tasks/'+task.id+'/draft',{study:study.id,version:task.version,...draft}).then(()=>{saved.current=serial;if(JSON.stringify(latest.current)===serial){setSaving('Saved');onDirty(false);}}).catch(e=>{setSaving('Unsaved changes');setError(String(e));});
        },500);
        return ()=>clearTimeout(timer);
    },[draft,loaded]);
    useEffect(()=>{const guard=(event:BeforeUnloadEvent)=>{if(JSON.stringify(latest.current)!==saved.current){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[]);
    const correction=(sid:string,key:'value'|'citation',value:string)=>setDraft(d=>({...d,corrections:{...d.corrections,[sid]:{...(d.corrections[sid]||{value:'',citation:''}),[key]:value}}}));
    async function act(action:string) {
        setBusy(true);setError('');
        try {
            const corrections:Record<string,unknown>={};
            if(action==='correct') for(const target of task.targets){const item=draft.corrections[target.subject];const evidence=excerpts(records.find(r=>r.id===target.subject));const citation=evidence[Number(item?.citation)];if(!item?.value.trim()||item.citation===''||!citation)throw new Error('Choose a value and supporting passage for each affected sample.');corrections[target.subject]={value:item.value.trim(),status:'known',vocabulary_id:null,rationale:draft.reason||'Corrected against the selected evidence',evidence:[{source_version:citation.source_version,locator:citation.locator,quote:citation.quote}]};}
            const reason=draft.reason.trim()||(action==='unknown'?'Reviewed the available evidence; keep this information unresolved.':action==='defer'?'More information is needed before deciding.':'The reviewed recommendation matches the source evidence.');
            const result=await api<Study>('/review-tasks/'+task.id+'/decision',{study:study.id,version:task.version,action,reason,corrections,remember:draft.remember||false});await onDone(result);
        } catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
    }
    const canAccept=task.kind!=='failure'&&task.targets.every(t=>(t.recommendation||t.field)?.status==='known');
    return <section className="panel decision-panel" aria-label="Current decision"><p className="eyebrow">{task.status==='done'?'COMPLETED DECISION':'YOUR NEXT DECISION'}</p><h2>{task.title}</h2><p>{task.reason}</p><p><strong>{task.targets.length} affected records:</strong> {task.targets.map(t=>t.name).join(', ')}</p>
        {error&&<p className="error" role="alert">{error}</p>}
        {(expanded||editing?task.targets:task.targets.slice(0,1)).map(target=><article className="evidence-card" key={target.subject}><strong>{target.name}</strong>{target.review&&<p>Last decision: {target.review.reason} · {target.review.actor}</p>}<p>Current suggestion: <b>{(target.recommendation||target.field)?.value!=null?String((target.recommendation||target.field)?.value):'No supported resolution yet'}</b></p>{(target.recommendation||target.field)?.evidence.map((c,i)=><blockquote key={i}><mark>{c.quote}</mark><footer>{c.locator} · {records.find(r=>r.id===target.subject)?.sources.find(s=>s.version===c.source_version)?.data.location}</footer></blockquote>)}
            {editing&&(()=>{const options=excerpts(records.find(r=>r.id===target.subject));const d=draft.corrections[target.subject]||{value:'',citation:''};return <div className="correction"><label>Corrected {nice(task.field)} for {target.name}<input value={d.value} onChange={e=>correction(target.subject,'value',e.target.value)}/></label><label>Supporting source passage<select value={d.citation} onChange={e=>correction(target.subject,'citation',e.target.value)}><option value="">Choose evidence…</option>{options.map((c,i)=><option value={i} key={i}>{c.locator}: {c.quote.slice(0,180)}</option>)}</select></label>{d.citation!==''&&options[Number(d.citation)]&&<blockquote>{options[Number(d.citation)].quote}</blockquote>}</div>;})()}
        </article>)}
        {task.targets.length>1&&!editing&&<button className="text-button" onClick={()=>setExpanded(!expanded)}>{expanded?'Collapse repeated evidence':`Inspect all ${task.targets.length} affected samples`}</button>}
        {editing&&task.targets.length>1&&<button className="secondary" onClick={()=>{
            const first=task.targets[0], selected=draft.corrections[first.subject];
            const quote=excerpts(records.find(r=>r.id===first.subject))[Number(selected?.citation)]?.quote;
            if(!selected?.value||selected.citation===''||!quote){setError('Choose a value and passage for the first sample.');return;}
            const corrections={...draft.corrections};let count=0;
            for(const target of task.targets){const index=excerpts(records.find(r=>r.id===target.subject)).findIndex(c=>c.quote===quote);if(index>=0){corrections[target.subject]={value:selected.value,citation:String(index)};count++;}}
            setDraft(d=>({...d,corrections}));setError(`Correction filled for ${count} samples with the identical passage. Check the affected set before saving.`);
        }}>Fill matching samples from the first correction</button>}
        {editing&&<label className="checkbox"><input type="checkbox" checked={draft.remember||false} onChange={e=>setDraft(d=>({...d,remember:e.target.checked}))}/>Use this correction as a future example after sign-off, when species, tissue and assay are known.</label>}
        <label>Review note (optional)<textarea aria-label="Review note" value={draft.reason} onChange={e=>setDraft(d=>({...d,reason:e.target.value}))} placeholder="Explain a correction or what AI should investigate."/></label><small role="status">{saving}</small>
        <div className="task-actions"><button disabled={busy||disabled||!loaded||saving!=='Saved'||!(editing||canAccept)} onClick={()=>void act(editing?'correct':'accept')}>{editing?'Correct and next':'Accept and next'}</button>{task.kind!=='failure'&&<button className="secondary" disabled={disabled} onClick={()=>void(async()=>{if(!editing){setBusy(true);try{setRecords(await Promise.all(task.targets.map(t=>api<RecordRow>('/subjects/'+t.subject))));setEditing(true);}catch(e){setError(String(e));}finally{setBusy(false);}}else setEditing(false);})()}>{editing?'Cancel correction':'Correct inline'}</button>}<button className="secondary" disabled={busy||disabled||saving!=='Saved'} onClick={()=>void act('unknown')}>{task.kind==='failure'?'Acknowledge failure and exclude unsupported values':'Keep unknown'}</button>{task.kind!=='failure'&&<button className="secondary" disabled={busy||disabled||saving!=='Saved'} onClick={()=>void act('defer')}>Defer</button>}<button className="secondary" disabled={busy||disabled||study.preparation.status!=='COMPLETED'||!provider||saving!=='Saved'} onClick={()=>void(async()=>{setBusy(true);try{await api('/review-tasks/'+task.id+'/investigate',{study:study.id,version:task.version,guidance:draft.reason,provider});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}})()}>Investigate further</button></div>
        <details><summary>Add supporting evidence here</summary><label>Sample<select value={evidenceTarget} onChange={e=>updateEvidence('subject',e.target.value)}>{task.targets.map(t=><option value={t.subject} key={t.subject}>{t.name}</option>)}</select></label><label>Source label<input value={evidenceLabel} onChange={e=>updateEvidence('label',e.target.value)} placeholder="Publication / supplementary table"/></label><label>Relevant source text<textarea maxLength={16000} value={newEvidence} onChange={e=>updateEvidence('text',e.target.value)}/></label><button className="secondary" disabled={disabled||busy||!newEvidence.trim()||!evidenceLabel.trim()} onClick={()=>void(async()=>{setBusy(true);try{const record=records.find(r=>r.id===evidenceTarget);if(!record)return;await api('/subjects/'+record.id+'/evidence',{version:record.version,text:newEvidence,label:evidenceLabel,classification:record.classification});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}})()}>Attach evidence</button></details>
    </section>;
}
