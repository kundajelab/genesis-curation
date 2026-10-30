import {useEffect,useState} from 'react';
import type {API} from './StudyWorkspace';
type Job={id:string;status:string;error?:string;result?:{failed?:number;prepared?:number;needs_source?:number};wait_reason?:string;queue_position?:number;progress?:{phase?:string;elapsed_seconds?:number;stale?:boolean;source?:string;study?:string;records?:number;completed?:number;total?:number;output_characters?:number}};
export function JobProgress({api,id}:{api:API;id:string}){
 const [job,setJob]=useState<Job|null>(null),[error,setError]=useState('');
 useEffect(()=>{if(!id)return;let alive=true,inflight=false,finished=false;setJob(null);setError('');
 const poll=async()=>{if(inflight||finished||document.hidden)return;inflight=true;try{const value=await api<Job>('/jobs/'+id);if(alive){setJob(value);setError('');finished=!['RUNNING','QUEUED'].includes(value.status);}}catch(e){if(alive)setError(String(e));}finally{inflight=false;}};
 void poll();const timer=setInterval(()=>void poll(),1000);return()=>{alive=false;clearInterval(timer);};},[id]);
 if(!id)return null;
 const p=job?.progress,active=!job||['RUNNING','QUEUED'].includes(job.status);
 return <section className="progress-card" aria-label="Work progress" aria-busy={active}>
 <div role="status" aria-live="polite"><strong>{error?'Connection interrupted':!job?'Starting…':job.status==='QUEUED'?`Queued${job.queue_position?' · position '+job.queue_position:''}`:job.status==='RUNNING'?(p?.stale?'Waiting for worker update':p?.phase||'Working…'):job.status==='SUCCEEDED'?(job.result?.failed?`Finished · ${job.result.failed} need attention`:job.result?.needs_source?`Finished · ${job.result.needs_source} need sources`:job.result?.prepared!=null?`${job.result.prepared} records prepared`:'Finished'):job.status==='CANCELLED'?'Paused / cancelled':job.status==='INTERRUPTED'?'Interrupted — resume when ready':'Needs attention'}</strong></div>
 {active&&<span className="work-indicator" aria-hidden="true"/>}
 {job?.wait_reason&&<small>{job.wait_reason}</small>}
 {p?.source&&<span>{p.source}</span>}{p?.records!=null&&<span>{p.records} records in this batch</span>}
 {p?.completed!=null&&p.total!=null&&<span>{p.completed} / {p.total} processed</span>}
 {p?.output_characters!=null&&p.output_characters>0&&<span>{p.output_characters.toLocaleString()} output characters received</span>}
 {p?.elapsed_seconds!=null&&<small>{Math.floor(p.elapsed_seconds)}s this step</small>}
 {error&&<small role="alert">Trying to reconnect. Your work is saved on the server.</small>}
 {job?.error&&<p role="alert">{job.error}</p>}
 {p?.stale&&<small>The last worker update is over 10 seconds old. Do not resubmit this request.</small>}
 </section>;
}
