import {useEffect,useState} from 'react';
import {API,Decision,Study,Task} from './StudyWorkspace';
import {MiningDecision,MiningQuestion} from './Mining';

type Summary=Partial<MiningQuestion>&{kind?:string;id:string;study:string;study_name:string;title:string;affected:number};
type Queue={waiting:{id:string;name:string;status:string}[];items:Summary[];total:number;preparing:number;ready:{id:string;name:string;version:string}[]};
export function GuidedReview({api,provider,onDirty,onBrowse}:{api:API;provider:string;onDirty:(value:boolean)=>void;onBrowse:()=>void}) {
    const [queue,setQueue]=useState<Queue|null>(null),[current,setCurrent]=useState<{study:Study;task:Task}|null>(null);
    const [mining,setMining]=useState<MiningQuestion|null>(null);
    const [started,setStarted]=useState(false),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[deferred,setDeferred]=useState(false);
    async function refresh(next=false){
        const q=await api<Queue>('/review-queue?status='+(deferred?'waiting':'open'));setQueue(q);
        if(next&&q.items[0])await open(q.items[0]);else if(next){setCurrent(null);setMining(null);await api('/review-tasks/guided/draft',{});}
    }
    async function open(item:Summary){if(item.kind==='mining-inclusion'){setMining(item as MiningQuestion);setCurrent(null);setStarted(true);await api('/review-tasks/guided/draft',{mining:item.run,candidate:item.id});return;}setMining(null);const value=await api<{study:Study;task:Task}>('/review-queue/'+item.study+'/'+item.id);setCurrent(value);setStarted(true);await api('/review-tasks/guided/draft',{study:item.study,task:item.id});}
    useEffect(()=>{if(dirty)return;const poll=()=>void refresh().catch(e=>setError(String(e)));poll();const timer=setInterval(poll,5000);return()=>clearInterval(timer);},[deferred,dirty]);
    useEffect(()=>{void api<{study?:string;task?:string;mining?:string;candidate?:string}>('/review-tasks/guided/draft').then(async saved=>{if(saved.mining&&saved.candidate){const run=await api<{goal:string;candidates:({status:string}&MiningQuestion)[]}>('/mining-runs/'+saved.mining);const candidate=run.candidates.find(c=>c.id===saved.candidate&&c.status==='uncertain');if(candidate){setMining({...candidate,run:saved.mining,goal:run.goal});setStarted(true);}}if(saved.study&&saved.task){try{const value=await api<{study:Study;task:Task}>('/review-queue/'+saved.study+'/'+saved.task);if(value.task.status==='open'&&value.study.preparation.status==='COMPLETED'){setCurrent(value);setStarted(true);}}catch{/* A completed/changed task is replaced by the current queue. */}}}).catch(e=>setError(String(e)));},[]);
    return <section className="guided-review"><section className="panel"><h2>Review queue</h2><p>{queue?.total??'…'} {deferred?'deferred':'open'} decisions · {queue?.ready.length??0} studies ready for sign-off · {queue?.preparing??0} preparing</p>
        {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
        <div className="task-actions"><button disabled={dirty||busy||!queue?.items.length} onClick={()=>void(async()=>{setBusy(true);try{if(queue?.items[0])await open(queue.items[0]);}catch(e){setError(String(e));}finally{setBusy(false);}})()}>{started?'Continue review':'Start review'}</button><button className="secondary" disabled={dirty} onClick={()=>{setDeferred(!deferred);setCurrent(null);setMining(null);}}>{deferred?'Back to open decisions':'Deferred decisions'}</button><button className="text-button" disabled={dirty} onClick={onBrowse}>Add or browse studies</button></div></section>
        {mining&&<MiningDecision key={mining.id+mining.version} api={api} item={mining} onDirty={value=>{setDirty(value);onDirty(value);}} onDone={async()=>{setMining(null);await refresh(true);}}/>}
        {current&&<><p><strong>{current.study.name}</strong> · Changes apply to the {current.task.targets.length} samples shown in this decision.</p><Decision key={current.task.id+':'+current.task.version} api={api} provider={provider} study={current.study} task={current.task} disabled={busy} onDirty={value=>{setDirty(value);onDirty(value);}} refresh={()=>refresh(true)} onDone={async()=>{setDirty(false);onDirty(false);setNotice('Decision saved. Progress is retained.');await refresh(true);}}/></>}
        {!current&&!mining&&started&&queue?.total===0&&<section className="panel"><h2>{deferred?'No deferred decisions.':'No open decisions.'}</h2><p>Prepared studies still need your final sign-off.</p></section>}
        {!!queue?.waiting.length&&<section className="panel"><h2>Preparation needs attention</h2>{queue.waiting.map(s=><p key={s.id}>{s.name} · {s.status==='NOT_STARTED'?'Ready to prepare':'Preparation interrupted'} <button className="secondary" disabled={dirty} onClick={()=>void api('/review-tasks/workspace/draft',{study:s.id}).then(onBrowse).catch(e=>setError(String(e)))}>Open preparation</button></p>)}</section>}
        {!!queue?.ready.length&&<section className="panel"><h2>Ready for final review</h2>{queue.ready.map(s=><p key={s.id}>{s.name} <button className="secondary" disabled={dirty} onClick={()=>void api('/review-tasks/workspace/draft',{study:s.id}).then(onBrowse).catch(e=>setError(String(e)))}>Review study summary</button></p>)}</section>}
    </section>;
}
