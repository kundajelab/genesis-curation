import {useEffect,useState} from 'react';
import {API,Decision,Study,Task} from './StudyWorkspace';

type Summary={id:string;study:string;study_name:string;title:string;affected:number};
type Queue={items:Summary[];total:number;preparing:number;ready:{id:string;name:string;version:string}[]};
export function GuidedReview({api,provider,onDirty,onBrowse}:{api:API;provider:string;onDirty:(value:boolean)=>void;onBrowse:()=>void}) {
    const [queue,setQueue]=useState<Queue|null>(null),[current,setCurrent]=useState<{study:Study;task:Task}|null>(null);
    const [started,setStarted]=useState(false),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[deferred,setDeferred]=useState(false);
    async function refresh(next=false){
        const q=await api<Queue>('/review-queue?status='+(deferred?'waiting':'open'));setQueue(q);
        if(next&&q.items[0])await open(q.items[0]);else if(next){setCurrent(null);await api('/review-tasks/guided/draft',{});}
    }
    async function open(item:Summary){const value=await api<{study:Study;task:Task}>('/review-queue/'+item.study+'/'+item.id);setCurrent(value);setStarted(true);await api('/review-tasks/guided/draft',{study:item.study,task:item.id});}
    useEffect(()=>{void refresh().catch(e=>setError(String(e)));},[deferred]);
    useEffect(()=>{void api<{study?:string;task?:string}>('/review-tasks/guided/draft').then(async saved=>{if(saved.study&&saved.task){try{const value=await api<{study:Study;task:Task}>('/review-queue/'+saved.study+'/'+saved.task);if(value.task.status==='open'){setCurrent(value);setStarted(true);}}catch{/* A completed/changed task is replaced by the current queue. */}}}).catch(e=>setError(String(e)));},[]);
    return <section className="guided-review"><section className="panel"><p className="eyebrow">YOUR NEXT DECISION</p><h2>AI prepares. You finish the exceptions.</h2><p>{queue?.total??'…'} {deferred?'deferred':'open'} decisions · {queue?.ready.length??0} studies ready for sign-off · {queue?.preparing??0} preparing</p>
        {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
        <div className="task-actions"><button disabled={dirty||busy||!queue?.items.length} onClick={()=>void(async()=>{setBusy(true);try{if(queue?.items[0])await open(queue.items[0]);}catch(e){setError(String(e));}finally{setBusy(false);}})()}>{started?'Continue review':'Start review'}</button><button className="secondary" disabled={dirty} onClick={()=>{setDeferred(!deferred);setCurrent(null);}}>{deferred?'Back to open decisions':'Deferred decisions'}</button><button className="text-button" disabled={dirty} onClick={onBrowse}>Add or browse studies</button></div></section>
        {current&&<><p><strong>{current.study.name}</strong> · Changes apply to the {current.task.targets.length} samples shown in this decision.</p><Decision key={current.task.id+':'+current.task.version} api={api} provider={provider} study={current.study} task={current.task} disabled={busy} onDirty={value=>{setDirty(value);onDirty(value);}} refresh={()=>refresh(true)} onDone={async()=>{setDirty(false);onDirty(false);setNotice('Decision saved. Progress is retained.');await refresh(true);}}/></>}
        {!current&&started&&queue?.total===0&&<section className="panel"><h2>{deferred?'No deferred decisions.':'Your exception list is clear.'}</h2><p>Prepared studies still need your final sign-off.</p></section>}
        {!!queue?.ready.length&&<section className="panel"><h2>Ready for final review</h2>{queue.ready.map(s=><p key={s.id}>{s.name} <button className="secondary" disabled={dirty} onClick={()=>void api('/review-tasks/workspace/draft',{study:s.id}).then(onBrowse).catch(e=>setError(String(e)))}>Review study summary</button></p>)}</section>}
    </section>;
}
