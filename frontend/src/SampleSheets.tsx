import {useEffect,useState} from 'react';
import type {API} from './StudyWorkspace';
import {JobProgress} from './JobProgress';

type Workflow={in_sheet?:number;runs?:number;precision?:number;recall?:number;traps?:string;error?:string;files:string[]};
type Campaign={id:string;species:string;created:string;workflows:Record<string,Workflow>};
type Overview={species:string[];workflows:string[];campaigns:Campaign[];atac_tab:boolean};
const names:Record<string,string>={'rna-seq':'RNA-seq','atac-seq':'Bulk ATAC','dap-seq':'DAP-seq','tss-seq':'TSS mapping','scatac-reads':'scATAC from reads'};
const species=(s:string)=>s.replaceAll('_',' ');

export function SampleSheets({api}:{api:API}){
 const [data,setData]=useState<Overview|null>(null),[chosen,setChosen]=useState(''),[workflows,setWorkflows]=useState<string[]>([]);
 const [job,setJob]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function refresh(){const d=await api<Overview>('/sheets');setData(d);setChosen(c=>c||d.species[0]||'');setWorkflows(w=>w.length?w:d.workflows);}
 useEffect(()=>{void refresh().catch(e=>setError(String(e)));const timer=setInterval(()=>void refresh().catch(e=>setError(String(e))),5000);return()=>clearInterval(timer);},[]);
 async function start(){setBusy(true);setError('');try{const r=await api<{job:string}>('/sheets/campaigns',{species:chosen,workflows});setJob(r.job);}catch(e){setError(String(e instanceof Error?e.message:e));}finally{setBusy(false);}}
 const toggle=(w:string,on:boolean)=>setWorkflows(ws=>on?[...ws,w]:ws.filter(x=>x!==w));
 return <section className="panel" aria-label="Sample sheets">
  <h3>Build sample sheets</h3>
  <p>Builds genesis-preprocessing sample sheets for one species from ENA and SRA metadata, the same as <code>genesis sheets campaign</code>. Reads are not measured: strandedness and other read checks come from the curated gold sheets, and each build is scored against them where they exist. Every left-out run has its reason in the runs-qc table.</p>
  {error&&<p role="alert" className="error">{error}</p>}
  {data&&<>
   <div className="form-grid"><label>Species<select value={chosen} onChange={e=>setChosen(e.target.value)}>{data.species.map(s=><option key={s} value={s}>{species(s)}</option>)}</select></label></div>
   <fieldset><legend>Workflows</legend>{data.workflows.map(w=><label className="checkbox" key={w}><input type="checkbox" checked={workflows.includes(w)} onChange={e=>toggle(w,e.target.checked)}/>{names[w]||w}</label>)}</fieldset>
   {workflows.includes('atac-seq')&&!data.atac_tab&&<p role="note">Bulk ATAC will miss the runs ENA does not label ATAC-seq and the GSA runs. To include them, export the metadata sheet's Bulk ATAC tab as TSV and copy it to <code>sheets/inputs/bulk-atac-tab.tsv</code> in the runtime.</p>}
   <button disabled={busy||!chosen||!workflows.length} onClick={()=>void start()}>Build sheets</button>
   <p><small>A first build of A. thaliana RNA-seq queries about 140,000 runs and takes tens of minutes; later builds reuse the archive cache.</small></p>
  </>}
  {job&&<JobProgress api={api} id={job}/>}
  <h3>Builds</h3>
  {!data?.campaigns.length&&<p>No builds yet.</p>}
  {data?.campaigns.map(c=><details key={c.id} open={c.id===data.campaigns[0].id}><summary>{species(c.species)} · {new Date(c.created).toLocaleString()}</summary>
   <div className="summary-table"><table><thead><tr><th>Workflow</th><th>In sheet</th><th>Candidates</th><th>Precision</th><th>Recall</th><th>Traps</th><th>Files</th></tr></thead><tbody>
    {Object.entries(c.workflows).map(([w,r])=><tr key={w}><td>{names[w]||w}</td>{r.error?<td colSpan={5} role="alert">{r.error}</td>:<><td>{r.in_sheet??'–'}</td><td>{r.runs??'–'}</td><td>{r.precision??'no gold'}</td><td>{r.recall??''}</td><td>{r.traps??''}</td></>}<td>{r.files.map(f=><a key={f} href={`/api/sheets/campaigns/${c.id}/${w}/${f}`}>{f.endsWith('.json')?'score':f.includes('runs-qc')?'runs-qc':'sheet'} </a>)}</td></tr>)}
   </tbody></table></div>
   {Object.values(c.workflows).some(r=>!r.error&&r.in_sheet===0)&&<small>An empty build with no gold to reuse means the runs need read measurement: run <code>genesis sheets build</code> with references.</small>}
  </details>)}
 </section>;
}
