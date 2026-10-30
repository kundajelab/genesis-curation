import { useEffect, useRef, useState } from 'react';

export type API = <T>(path: string, data?: unknown) => Promise<T>;
type Citation = {source_version: string; locator: string; quote: string};
type Field = {field: string; value: string | number | null; status: string; rationale: string; evidence: Citation[]};
type Source = {version: string; data: {snapshot: string; location: string}};
type RecordRow = {preparation_provenance?:{coverage?:{available_passages?:number;selected_passages?:number;omitted_passages?:number}};id: string; name: string; version: string; classification: string; fields: Field[]; sources: Source[]; reviews: Record<string,{action:string}>};
type Target = {subject: string; name: string; version: string; field: Field | null; recommendation: Field | null; review?: {reason: string; actor: string}};
export type Task = {id: string; study: string; version: string; title: string; reason: string; kind: string; field: string; status: string; targets: Target[]};
export type Study = {id: string; name: string; version: string; status: string; member_count: number; pending: number; completed_tasks: number; approval_current: boolean; approval: string | null; records: RecordRow[]; tasks: Task[]; preparation: {status: string; mode?: string; job?: string; completed: Record<string,string>; failures: Record<string,string>; retrieval: {url: string; status: string; reason?: string}[]}};
type Draft = {targets?:string[]; reason: string; remember?: boolean; evidence?: {text:string;label:string;subject:string}; corrections: Record<string, {value: string; citation: string}>};
const nice = (value: string) => value.replaceAll('_', ' ');

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

export function AddStudy({api,provider,onAdded}: {api: API;provider:string;onAdded:(result:{job?:string;studies?:string[]})=>Promise<void>}) {
    const [accession,setAccession]=useState(''), [text,setText]=useState(''), [filename,setFilename]=useState(''), [namespace,setNamespace]=useState(''), [id,setId]=useState(''), [columns,setColumns]=useState<string[]>([]), [count,setCount]=useState(0), [classification,setClassification]=useState('local-only'), [busy,setBusy]=useState(false), [error,setError]=useState('');
    async function submit(body:unknown) {setBusy(true);setError('');try{await onAdded(await api('/studies',body));}catch(e){setError(String(e));}finally{setBusy(false);}}
    return <section className="panel add-study"><h2>Add a study</h2>{error&&<p role="alert" className="error">{error}</p>}<form onSubmit={e=>{e.preventDefault();void submit({accession,provider});}}><label>Public study or sample accession<input value={accession} onChange={e=>setAccession(e.target.value)} placeholder="GSE… or PRJNA…" required/></label><button disabled={busy}>Retrieve and prepare</button></form><div className="import-divider">or upload a metadata table</div><label>CSV, TSV or JSON · up to 2 MiB per file<input type="file" accept=".csv,.tsv,.json" onChange={e=>{const file=e.target.files?.[0];if(!file)return;void(async()=>{try{if(file.size>2*1024*1024)throw new Error('Split files larger than 2 MiB.');const value=await file.text();const preview=await api<{rows:unknown[];columns:string[]}>('/imports/preview',{text:value,filename:file.name});setText(value);setFilename(file.name);setNamespace(file.name.replace(/\.[^.]+$/,''));setColumns(preview.columns);setCount(preview.rows.length);setId(preview.columns.find(c=>['id','accession','sample_id','run_accession'].includes(c))||preview.columns[0]);}catch(err){setError(String(err));}})();}}/></label>{text&&<form onSubmit={e=>{e.preventDefault();void submit({text,filename,namespace,id_column:id,classification,provider});}}><p>{count} records ready to import. A study_accession column groups separate studies automatically.</p><label>Study/import name<input required value={namespace} onChange={e=>setNamespace(e.target.value)}/></label><label>Unique record identifier<select value={id} onChange={e=>setId(e.target.value)}>{columns.map(c=><option key={c}>{c}</option>)}</select></label><label>Sharing classification<select value={classification} onChange={e=>setClassification(e.target.value)}><option value="local-only">Local only</option><option value="public">Public metadata</option><option value="internal">Internal</option></select></label><button disabled={busy}>Import and prepare</button></form>}</section>;
}

export function Decision({api,study,task,provider,disabled,onDirty,onDone,refresh}: {api:API;study:Study;task:Task;provider:string;disabled:boolean;onDirty:(value:boolean)=>void;onDone:(study:Study)=>Promise<void>;refresh:()=>Promise<void>}) {
    const [draft,setDraft]=useState<Draft>({reason:'',corrections:{}}), [loaded,setLoaded]=useState(false), [saving,setSaving]=useState('Saved'), [error,setError]=useState(''), [busy,setBusy]=useState(false), [editing,setEditing]=useState(false);
    const [records,setRecords]=useState(study.records), [expanded,setExpanded]=useState(false);
    const selectedTargets=task.targets.filter(t=>!draft.targets||draft.targets.includes(t.subject));
    const [targetPage,setTargetPage]=useState(0);
    const evidenceTarget=draft.evidence?.subject||task.targets[0]?.subject||'';
    const evidenceLabel=draft.evidence?.label||'', newEvidence=draft.evidence?.text||'';
    const updateEvidence=(key:'text'|'label'|'subject',value:string)=>setDraft(d=>({...d,evidence:{text:newEvidence,label:evidenceLabel,subject:evidenceTarget,...d.evidence,[key]:value}}));
    const latest=useRef(draft);latest.current=draft;
    const saved=useRef('');
    useEffect(()=>{
        let live=true;
        void api<Draft & {version?:string}>('/review-tasks/'+task.id+'/draft').then(d=>{if(!live)return;const restored={targets:d.version===task.version?d.targets:undefined,reason:d.reason||'',corrections:d.version===task.version?d.corrections||{}:{},evidence:d.evidence,remember:d.remember||false};setDraft(restored);setEditing(Object.keys(restored.corrections).length>0);saved.current=JSON.stringify(restored);setLoaded(true);if(d.version&&d.version!==task.version)setError('Evidence changed since your saved draft. Your note is retained; check the updated samples.');}).catch(e=>setError(String(e)));
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
    useEffect(()=>{if(editing)void Promise.all(task.targets.slice(targetPage*25,(targetPage+1)*25).map(t=>api<RecordRow>('/subjects/'+t.subject))).then(loaded=>setRecords(old=>[...old.filter(r=>!loaded.some(n=>n.id===r.id)),...loaded])).catch(e=>setError(String(e)));},[targetPage,editing]);
    const correction=(sid:string,key:'value'|'citation',value:string)=>setDraft(d=>({...d,corrections:{...d.corrections,[sid]:{...(d.corrections[sid]||{value:'',citation:''}),[key]:value}}}));
    async function act(action:string) {
        setBusy(true);setError('');
        try {
            const corrections:Record<string,unknown>={};
            if(action==='correct') for(const target of selectedTargets){const item=draft.corrections[target.subject];const evidence=excerpts(records.find(r=>r.id===target.subject));const citation=evidence[Number(item?.citation)];if(!item?.value.trim()||item.citation===''||!citation)throw new Error('Choose a value and supporting passage for each affected sample.');corrections[target.subject]={value:item.value.trim(),status:'known',vocabulary_id:null,rationale:draft.reason||'Corrected against the selected evidence',evidence:[{source_version:citation.source_version,locator:citation.locator,quote:citation.quote}]};}
            const reason=draft.reason.trim()||(action==='unknown'?'Reviewed the available evidence; keep this information unresolved.':action==='defer'?'More information is needed before deciding.':'The reviewed recommendation matches the source evidence.');
            const result=await api<Study>('/review-tasks/'+task.id+'/decision',{study:study.id,version:task.version,action,reason,corrections,targets:selectedTargets.map(t=>t.subject),remember:draft.remember||false});await onDone(result);
        } catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
    }
    const canAccept=task.kind!=='failure'&&selectedTargets.length>0&&selectedTargets.every(t=>(t.recommendation||t.field)?.status==='known');
    return <section className="panel decision-panel" aria-label="Current decision"><p className="eyebrow">{task.status==='done'?'COMPLETED DECISION':'STUDY QUESTION'}</p><h2>{task.title}</h2><p>{task.reason}</p><p><strong>{task.targets.length} affected records:</strong> {task.targets.slice(0,5).map(t=>t.name).join(', ')}{task.targets.length>5?'…':''}</p>
        {error&&<p className="error" role="alert">{error}</p>}
        {(expanded||editing?task.targets.slice(targetPage*25,(targetPage+1)*25):task.targets.slice(0,1)).map(target=><article className="evidence-card" key={target.subject}><label className="checkbox"><input type="checkbox" checked={!draft.targets||draft.targets.includes(target.subject)} disabled={busy||disabled} onChange={e=>setDraft(d=>({...d,targets:e.target.checked?[...new Set([...(d.targets||task.targets.map(t=>t.subject)),target.subject])]:(d.targets||task.targets.map(t=>t.subject)).filter(id=>id!==target.subject)}))}/><strong>{target.name}</strong></label>{target.review&&<p>Last decision: {target.review.reason} · {target.review.actor}</p>}<p>Current suggestion: <b>{(target.recommendation||target.field)?.value!=null?String((target.recommendation||target.field)?.value):'No supported resolution yet'}</b></p>{(target.recommendation||target.field)?.evidence.map((c,i)=><blockquote key={i}><mark>{c.quote}</mark><footer>{c.locator} · {records.find(r=>r.id===target.subject)?.sources.find(s=>s.version===c.source_version)?.data.location}</footer></blockquote>)}
            {editing&&(()=>{const options=excerpts(records.find(r=>r.id===target.subject));const d=draft.corrections[target.subject]||{value:'',citation:''};return <div className="correction"><label>Corrected {nice(task.field)} for {target.name}<input value={d.value} onChange={e=>correction(target.subject,'value',e.target.value)}/></label><label>Supporting source passage<select value={d.citation} onChange={e=>correction(target.subject,'citation',e.target.value)}><option value="">Choose evidence…</option>{options.map((c,i)=><option value={i} key={i}>{c.locator}: {c.quote.slice(0,180)}</option>)}</select></label>{d.citation!==''&&options[Number(d.citation)]&&<blockquote>{options[Number(d.citation)].quote}</blockquote>}</div>;})()}
        </article>)}
        {task.targets.length>1&&!editing&&<button className="text-button" onClick={()=>setExpanded(!expanded)}>{expanded?'Collapse repeated evidence':`Inspect all ${task.targets.length} affected samples`}</button>}
        {(expanded||editing)&&task.targets.length>25&&<div className="task-actions"><button disabled={!targetPage} onClick={()=>setTargetPage(p=>p-1)}>Previous samples</button><span>{targetPage*25+1}–{Math.min((targetPage+1)*25,task.targets.length)}</span><button disabled={(targetPage+1)*25>=task.targets.length} onClick={()=>setTargetPage(p=>p+1)}>Next samples</button></div>}
        <p>{selectedTargets.length} samples selected for this decision.</p>
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
        <div className="task-actions"><button disabled={busy||disabled||!loaded||!selectedTargets.length||saving!=='Saved'||!(editing||canAccept)} onClick={()=>void act(editing?'correct':'accept')}>{editing?'Save correction':'Accept suggestion'}</button>{task.kind!=='failure'&&<button className="secondary" disabled={disabled} onClick={()=>void(async()=>{if(!editing){setBusy(true);try{const loaded=await Promise.all(task.targets.slice(targetPage*25,(targetPage+1)*25).map(t=>api<RecordRow>('/subjects/'+t.subject)));setRecords(old=>[...old.filter(r=>!loaded.some(n=>n.id===r.id)),...loaded]);setEditing(true);}catch(e){setError(String(e));}finally{setBusy(false);}}else setEditing(false);})()}>{editing?'Cancel correction':'Correct inline'}</button>}<button className="secondary" disabled={busy||disabled||!selectedTargets.length||saving!=='Saved'} onClick={()=>void act('unknown')}>{task.kind==='failure'?'Acknowledge failure and exclude unsupported values':'Keep unknown'}</button>{task.kind!=='failure'&&<button className="secondary" disabled={busy||disabled||!selectedTargets.length||saving!=='Saved'} onClick={()=>void act('defer')}>Defer</button>}<button className="secondary" disabled={busy||disabled||study.preparation.status!=='COMPLETED'||!provider||saving!=='Saved'} onClick={()=>void(async()=>{setBusy(true);try{await api('/review-tasks/'+task.id+'/investigate',{study:study.id,version:task.version,guidance:draft.reason,provider});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}})()}>Investigate further</button></div>
        <details><summary>Add supporting evidence here</summary><label>Sample<select value={evidenceTarget} onChange={e=>updateEvidence('subject',e.target.value)}>{task.targets.map(t=><option value={t.subject} key={t.subject}>{t.name}</option>)}</select></label><label>Source label<input value={evidenceLabel} onChange={e=>updateEvidence('label',e.target.value)} placeholder="Publication / supplementary table"/></label><label>Relevant source text<textarea maxLength={16000} value={newEvidence} onChange={e=>updateEvidence('text',e.target.value)}/></label><button className="secondary" disabled={disabled||busy||!newEvidence.trim()||!evidenceLabel.trim()} onClick={()=>void(async()=>{setBusy(true);try{const record=records.find(r=>r.id===evidenceTarget);if(!record)return;await api('/subjects/'+record.id+'/evidence',{version:record.version,text:newEvidence,label:evidenceLabel,classification:record.classification});await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}})()}>Attach evidence</button></details>
    </section>;
}
