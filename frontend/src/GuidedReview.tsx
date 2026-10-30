import {useEffect,useRef,useState} from 'react';
import {API,Decision,Study,Task} from './StudyWorkspace';

type Summary={id:string;name:string;accession:string|null;version:string;status:string;member_count:number;pending:number;completed_tasks:number;approval_current:boolean;coverage_pending:number;preparation:string;assays:string[]};
type Item={id:string;title:string;reason:string;affected:number;status:string};
type Checklist={study:Summary;items:Item[];total:number;next_cursor:number|null};
type Page={items:Summary[];total:number;next_cursor:number|null};
const labels:Record<string,string>={signed:'Signed off',preparing:'Preparing',needs_decisions:'Needs review',ready:'Ready to sign off',not_prepared:'Needs preparation'};
export function GuidedReview({api,provider,onDirty,onBrowse}:{api:API;provider:string;onDirty:(value:boolean)=>void;onBrowse:()=>void}) {
    const [page,setPage]=useState<Page|null>(null),[checklist,setChecklist]=useState<Checklist|null>(null);
    const [current,setCurrent]=useState<{study:Study;task:Task}|null>(null),[selected,setSelected]=useState('');
    const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
    const [cursor,setCursor]=useState(0),[taskCursor,setTaskCursor]=useState(0),[history,setHistory]=useState<unknown[]|null>(null);
    const [storage,setStorage]=useState<{warning:boolean;blocked:boolean;paused:boolean;free_bytes:number}|null>(null);
    const selectedRef=useRef(selected);selectedRef.current=selected;
    async function refresh(){
        const [p,d]=await Promise.all([api<Page>('/study-review?cursor='+cursor),api<{warning:boolean;blocked:boolean;paused:boolean;free_bytes:number}>('/storage')]);setPage(p);setStorage(d);
        const key=selectedRef.current;
        if(key){const c=await api<Checklist>('/study-review/'+key+'?cursor='+taskCursor);if(selectedRef.current===key)setChecklist(c);}
    }
    async function work(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
    async function select(key:string){setSelected(key);selectedRef.current=key;setChecklist(null);setCurrent(null);setTaskCursor(0);setHistory(null);await api('/review-tasks/guided/draft',{study:key});}
    useEffect(()=>{void api<{study?:string}>('/review-tasks/guided/draft').then(saved=>{if(saved.study){setSelected(saved.study);selectedRef.current=saved.study;}}).catch(e=>setError(String(e)));},[]);
    useEffect(()=>{if(dirty)return;void refresh().catch(e=>setError(String(e)));const timer=setInterval(()=>void refresh().catch(e=>setError(String(e))),5000);return()=>clearInterval(timer);},[selected,cursor,taskCursor,dirty]);
    async function open(item:Item){if(current?.task.id===item.id){setCurrent(null);return;}setCurrent(await api('/review-queue/'+selected+'/'+item.id));}
    const s=checklist?.study;
    return <section className="study-workspace">
        <div className="section-title"><h2>Study review</h2><button className="secondary" disabled={dirty||busy} onClick={onBrowse}>Add studies</button></div>
        {storage?.warning&&<p role="alert" className="notice">Disk space: {(storage.free_bytes/1024**3).toFixed(1)} GiB free. {storage.blocked?'New work is blocked. Free space, then resume manually.':'Free space before starting a large import.'}</p>}
        {storage?.paused&&<p role="alert">Background work is paused for disk space. <button disabled={storage.blocked||busy} onClick={()=>void work(async()=>{await api('/storage/resume',{});await refresh();})}>Resume background work</button></p>}
        {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
        <div className="study-layout"><aside className="panel study-sidebar"><h3>Studies · {page?.total??'…'}</h3>
            {page?.items.map(study=><button className={'study-link '+(study.id===selected?'selected':'')} key={study.id} disabled={dirty||busy} onClick={()=>void work(()=>select(study.id))}><strong>{study.name}</strong><span>{labels[study.status]}</span><small>{study.member_count} samples · {study.pending} questions</small></button>)}
            {page?.total===0&&<p>No studies prepared yet. Select records in Curation or add an accession.</p>}
            <div className="task-actions"><button className="secondary" disabled={!cursor||dirty||busy} onClick={()=>setCursor(Math.max(0,cursor-25))}>Previous</button><button className="secondary" disabled={page?.next_cursor==null||dirty||busy} onClick={()=>setCursor(page?.next_cursor??0)}>More studies</button></div>
        </aside><div className="study-body">{!s?<section className="panel"><h3>{selected?'Loading study…':'Choose a study'}</h3><p>Resolve its checklist, inspect the summary, then sign off.</p></section>:<>
            <section className="panel"><p className="eyebrow">{labels[s.status]}</p><h2>{s.name}</h2><p>{s.member_count} samples{s.assays.length?' · '+s.assays.join(' / '):''}</p><p>{s.completed_tasks} resolved · {s.pending} remaining</p><progress aria-label="Study review progress" value={s.completed_tasks} max={Math.max(1,s.completed_tasks+s.pending)}/>
                {!!s.coverage_pending&&<p role="alert">{s.coverage_pending} known records still need retrieval. Sign-off is blocked until study coverage is complete. <button disabled={busy||dirty||!provider||storage?.blocked} onClick={()=>void work(async()=>{await api('/studies/'+s.id+'/retrieve',{provider});setNotice('Study retrieval queued. Progress is in Discover.');await refresh();})}>Curate remaining records</button></p>}
                {s.preparation!=='COMPLETED'&&<p>{s.preparation==='RUNNING'||s.preparation==='QUEUED'?'Preparation is running. You can return later.':'Preparation is incomplete.'} <button className="secondary" disabled={busy||dirty||storage?.blocked||['RUNNING','QUEUED'].includes(s.preparation)} onClick={()=>void work(async()=>{await api('/studies/'+s.id+'/prepare',{version:s.version,provider});await refresh();})}>Prepare study</button></p>}
            </section>
            <section className="panel"><h3>Checklist</h3>{checklist?.items.map(item=><div className="study-checklist-item" key={item.id}><button className="study-link" disabled={busy||dirty} aria-expanded={current?.task.id===item.id} onClick={()=>void work(()=>open(item))}><strong>{item.status==='done'?'✓ ':item.status==='waiting'?'Deferred · ':''}{item.title}</strong><small>{item.affected} affected samples · {item.status==='done'?'Resolved':item.reason}</small></button>
                {current?.task.id===item.id&&<Decision key={current.task.id+':'+current.task.version} api={api} provider={provider} study={current.study} task={current.task} disabled={busy||s.preparation!=='COMPLETED'||item.status==='done'} onDirty={value=>{setDirty(value);onDirty(value);}} refresh={async()=>{setCurrent(null);await refresh();}} onDone={async()=>{setDirty(false);onDirty(false);setCurrent(null);setNotice('Decision saved. Continue this study’s checklist.');await refresh();}}/>}
            </div>)}{!checklist?.items.length&&<p>{s.approval_current?'Study signed off.':s.preparation==='COMPLETED'?'No unresolved questions. Review the summary before signing.':'Questions appear after preparation.'}</p>}
                <div className="task-actions"><button className="secondary" disabled={!taskCursor||dirty||busy} onClick={()=>setTaskCursor(Math.max(0,taskCursor-50))}>Previous questions</button><button className="secondary" disabled={checklist?.next_cursor==null||dirty||busy} onClick={()=>setTaskCursor(checklist?.next_cursor??0)}>More questions</button></div>
            </section>
            <StudySummary api={api} study={s} onError={setError}/>
            <section className="panel"><h3>Sign-off</h3><p>Accept supported suggestions and reviewed decisions for this study. Unresolved values remain excluded.</p>
                {!s.approval_current?<button disabled={busy||dirty||s.status!=='ready'} onClick={()=>void work(async()=>{await api('/studies/'+s.id+'/approve',{version:s.version});setNotice('Study signed off.');await refresh();})}>Sign off study</button>:<div className="release-actions"><strong>Signed off</strong>{['json','csv','tsv'].map(format=><a key={format} href={'/api/studies/'+s.id+'/export?format='+format}>{format.toUpperCase()}</a>)}</div>}
                <button className="secondary" disabled={dirty||busy||!page?.items.some(x=>x.id!==s.id&&!x.approval_current)} onClick={()=>void work(async()=>{const next=page?.items.find(x=>x.id!==s.id&&!x.approval_current);if(next)await select(next.id);})}>Next study</button>
                <details><summary onClick={()=>void api<unknown[]>('/studies/'+s.id+'/history').then(setHistory).catch(e=>setError(String(e)))}>Change history</summary><pre>{history?JSON.stringify(history,null,2):'Loading…'}</pre></details>
            </section>
        </>}</div></div>
    </section>;
}
function StudySummary({api,study,onError}:{api:API;study:Summary;onError:(s:string)=>void}){
    const [open,setOpen]=useState(false),[cursor,setCursor]=useState(0),[page,setPage]=useState<{items:{name:string;fields:{field:string;value:unknown;original:unknown;status:string}[]}[];next_cursor:number|null}|null>(null);
    useEffect(()=>{setCursor(0);setPage(null);setOpen(false);},[study.id]);
    useEffect(()=>{if(open)void api<typeof page>('/study-review/'+study.id+'/samples?cursor='+cursor).then(setPage).catch(e=>onError(String(e)));},[open,cursor,study.id,study.version]);
    return <section className="panel"><button className="text-button" aria-expanded={open} onClick={()=>setOpen(!open)}>Sample summary · original → prepared values</button>{open&&<><div className="summary-table"><table><thead><tr><th>Sample</th><th>Changes and prepared values</th></tr></thead><tbody>{page?.items.map((r,i)=><tr key={i}><td>{r.name}</td><td>{r.fields.map(f=><div key={f.field}><strong>{f.field.replaceAll('_',' ')}</strong>: {String(f.original??'—')} → {String(f.value??'Unknown')} ({f.status})</div>)}</td></tr>)}</tbody></table></div><button disabled={!cursor} onClick={()=>setCursor(Math.max(0,cursor-25))}>Previous samples</button><button disabled={page?.next_cursor==null} onClick={()=>setCursor(page?.next_cursor??0)}>Next samples</button></>}</section>;
}
