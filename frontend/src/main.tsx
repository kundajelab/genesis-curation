import React, { useEffect, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { Inbox } from './Inbox';
type Citation = {
    source_version: string;
    locator: string;
    quote: string;
};
type Field = {
    field: string;
    original: unknown;
    value: string | number | null;
    status: string;
    rationale: string;
    evidence: Citation[];
    vocabulary_version: string;
};
type Source = {
    version: string;
    data: {
        location: string;
        snapshot: string;
    };
};
type Subject = {
    id: string;
    name: string;
    kind: string;
    classification: string;
    version: string;
    pending: number;
    uncertain: number;
    fields: Field[];
    sources: Source[];
    reviews: Record<string, {
        action: string;
        reason: string;
        actor: string;
    }>;
    metadata: Record<string, unknown>;
    messages: {
        question: string;
        answer: string;
        citations: Citation[];
    }[];
    proposal: string | null;
};
type Job = {
    id: string;
    kind: string;
    status: string;
    error: string | null;
    result?: {
        rows: Record<string, unknown>[];
        text: string;
        filename: string;
        id_column: string;
    };
};
type Provider = {
    id: string;
    model: string;
    mode: string;
    external: boolean;
    allowed_classifications: string[];
};
let csrf = '';
async function api<T>(path: string, data?: unknown): Promise<T> {
    const r = await fetch('/api' + path, { credentials: 'same-origin', method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: data === undefined ? undefined : JSON.stringify(data) });
    const v = await r.json();
    if (!r.ok)
        throw new Error(v.error || `Request failed (${r.status})`);
    return v;
}
const labels: Record<string, string> = { organism: 'Species', taxon: 'Taxonomy', developmental_stage: 'Developmental stage', biological_replicate: 'Biological replicate', technical_lane: 'Sequencing lane', reference_proposal: 'Reference genome', tf_identity: 'Transcription factor', tf_source_organism: 'Transcription factor species', dna_organism: 'DNA species', dna_genotype: 'DNA genotype', control_assignment_proposal: 'Control sample' };
const nice = (s: string) => labels[s] || s.replaceAll('_', ' ');
const priority = ['organism', 'tissue', 'assay', 'developmental_stage', 'genotype', 'cultivar', 'treatment', 'accession'];
const orderedFields = (s: Subject) => [...s.fields].sort((a, b) => (priority.includes(a.field) ? priority.indexOf(a.field) : 99) - (priority.includes(b.field) ? priority.indexOf(b.field) : 99));
const nextField = (s: Subject, after = '') => {
    const fields = orderedFields(s);
    const start = fields.findIndex(f => f.field === after) + 1;
    return [...fields.slice(start), ...fields.slice(0, start)].find(f => !s.reviews[f.field]);
};
const display = (v: unknown) => v === null || v === undefined ? 'Not recorded' : String(v);
function sourceExcerpts(source?: Source): {locator: string; quote: string}[] {
    if (!source) return [];
    const snapshot = source.data.snapshot;
    try {
        const result: {locator: string; quote: string}[] = [];
        const walk = (value: unknown, path: string) => {
            if (result.length >= 500) return;
            if (value !== null && typeof value === 'object') {
                Object.entries(value).forEach(([key, item]) => walk(item, path + '/' + key.replaceAll('~', '~0').replaceAll('/', '~1')));
            } else if (value !== null && value !== '') result.push({locator: path || '$', quote: String(value)});
        };
        walk(JSON.parse(snapshot), '');
        return result;
    } catch { return snapshot.split('\n').map((quote, i) => ({locator: `line:${i + 1}`, quote})).filter(item => item.quote.trim()).slice(0, 500); }
}
function App() {
    const [actor, setActor] = useState(''), [boot, setBoot] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
    const [subjects, setSubjects] = useState<Subject[]>([]), [active, setActive] = useState<Subject | null>(null), [jobs, setJobs] = useState<Job[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]), [provider, setProvider] = useState(''), [skillList, setSkillList] = useState<{
        id: string;
        title: string;
        sha256: string;
    }[]>([]), [skill, setSkill] = useState('plant-metadata');
    const [view, setView] = useState('inbox'), [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [selected, setSelected] = useState('');
    const [reason, setReason] = useState(''), [edit, setEdit] = useState(false), [value, setValue] = useState(''), [source, setSource] = useState(''), [locator, setLocator] = useState(''), [quote, setQuote] = useState('');
    const [question, setQuestion] = useState(''), [feedback, setFeedback] = useState(''), [busy, setBusy] = useState(false), [history, setHistory] = useState<unknown[] | null>(null);
    const [filename, setFilename] = useState(''), [text, setText] = useState(''), [preview, setPreview] = useState<Record<string, unknown>[]>([]), [idColumn, setIdColumn] = useState(''), [namespace, setNamespace] = useState(''), [classification, setClassification] = useState('local-only'), [accession, setAccession] = useState(''), [accessionJob, setAccessionJob] = useState('');
    const loadedAccession = useRef('');
    const [trackedJob, setTrackedJob] = useState<{id: string; subject: string} | null>(null);
    const currentJob = jobs.find(j => j.id === trackedJob?.id);
    const jobRunning = currentJob && ['QUEUED', 'RUNNING'].includes(currentJob.status);
    const reviewed = active ? active.fields.filter(f => active.reviews[f.field]).length : 0;
    const approved = active ? active.fields.filter(f => active.reviews[f.field]?.action === 'approve').length : 0;
    const selectedField = active?.fields.find(f => f.field === selected);
    const excerpts = sourceExcerpts(active?.sources.find(s => s.version === source));
    async function work(fn: () => Promise<void>) { setError(''); setNotice(''); setBusy(true); try {
        await fn();
    }
    catch (e) {
        setError(String(e instanceof Error ? e.message : e));
    }
    finally {
        setBusy(false);
    } }
    async function refresh() { const [s, j] = await Promise.all([api<Subject[]>('/subjects'), api<Job[]>('/jobs')]); setSubjects(s); setJobs(j); }
    async function open(id: string, field?: string) { const s = await api<Subject>('/subjects/' + id); setActive(s); setSelected(''); setHistory(null); setReason(''); setEdit(false); setFeedback(''); setQuestion(''); const first = s.fields.find(f => f.field === field) || nextField(s); if (first) choose(first, s); setView('review'); }
    useEffect(() => { api<{
        actor: string;
        csrf: string;
    }>('/session').then(s => { csrf = s.csrf; setActor(s.actor); }).catch(() => { }).finally(() => setBoot(false)); }, []);
    useEffect(() => {
        if (!actor)
            return;
        void work(async () => { await refresh(); const [p, s] = await Promise.all([api<Provider[]>('/providers'), api<typeof skillList>('/skills')]); setProviders(p); setProvider(p.find(x => x.mode !== 'disabled' && !x.external)?.id || p.find(x => x.mode !== 'disabled')?.id || ''); setSkillList(s); });
        const t = setInterval(() => { void refresh().catch(() => { }); }, 2500);
        return () => clearInterval(t);
    }, [actor]);
    useEffect(() => { if (!accessionJob)
        return; const found = jobs.find(j => j.id === accessionJob); if (found?.status === 'SUCCEEDED' && loadedAccession.current !== accessionJob) {
        loadedAccession.current = accessionJob;
        void api<Job>('/jobs/' + accessionJob).then(j => { if (j.result) {
            setPreview(j.result.rows);
            setText(j.result.text);
            setFilename(j.result.filename);
            setIdColumn(j.result.id_column);
            setNamespace(accession);
            setClassification('public');
        } }).catch(e => { loadedAccession.current = ''; setError(String(e)); });
    } }, [jobs, accessionJob]);
    useEffect(() => { setHistory(null); }, [active?.version]);
    useEffect(() => { if (!active)
        return; const allowed = providers.filter(p => p.mode !== 'disabled' && p.allowed_classifications.includes(active.classification)); if (!allowed.some(p => p.id === provider))
        setProvider(allowed[0]?.id || ''); }, [active?.classification, providers, provider]);
    function choose(f: Field, record = active) { setSelected(f.field); setReason(''); setEdit(false); setValue(f.value === null ? '' : String(f.value)); setSource(f.evidence[0]?.source_version || record?.sources[0]?.version || ''); setLocator(f.evidence[0]?.locator || 'line:1'); setQuote(f.evidence[0]?.quote || ''); }
    async function enqueue(kind: string) { if (!active)
        return; const j = await api<{id: string}>('/jobs', { kind, payload: { subject: active.id, version: active.version, provider, ...(kind === 'chat' ? { question } : { skill, feedback }) } }); setTrackedJob({id: j.id, subject: active.id}); setQuestion(''); setNotice('Working on this record. You can continue reviewing while you wait.'); await refresh(); }
    async function decide(action: string) {
        if (!active || !selectedField)
            return;
        const correction = edit ? { value, status: 'known', vocabulary_id: null, rationale: reason, evidence: [{ source_version: source, locator, quote }] } : undefined;
        const s = await api<Subject>('/subjects/' + active.id + '/decisions', { version: active.version, field: selected, action, reason, correction });
        setActive(s);
        setReason('');
        setEdit(false);
        document.querySelector('.detail')?.scrollIntoView({behavior: 'smooth', block: 'start'});
        await refresh();
        const next = nextField(s, selected); if (next) choose(next, s); else setSelected(''); setView('inbox'); setNotice('Decision saved. Your to-do list is up to date.');
    }
    async function loadFile(file: File) { if (file.size > 2 * 1024 * 1024)
        throw new Error('Split uploads larger than 2 MiB.'); const t = await file.text(); setFilename(file.name); setText(t); setAccessionJob(''); const p = await api<{
        rows: Record<string, unknown>[];
        columns: string[];
    }>('/imports/preview', { filename: file.name, text: t }); setPreview(p.rows); setIdColumn(p.columns.find(c => ['sample_id', 'accession', 'id', 'sample', 'run_accession'].includes(c.toLowerCase())) || p.columns[0]); setNamespace(file.name.replace(/\.[^.]+$/, '')); }
    if (boot)
        return <main className="login"><p>Opening Genesis…</p></main>;
    if (!actor)
        return <main className="login"><div className="login-card"><div className="brand">g<span>genesis</span></div><p className="eyebrow">METADATA CURATION</p><h1>Evidence first.<br />Human decisions.</h1><p>Sign in to review plant study metadata, trace proposals to their sources, and curate with confidence.</p><form onSubmit={e => { e.preventDefault(); const d = new FormData(e.currentTarget); void work(async () => { const s = await api<{
            actor: string;
            csrf: string;
        }>('/login', { username: d.get('username'), password: d.get('password') }); csrf = s.csrf; setActor(s.actor); }); }}><label>Curator<input name="username" autoComplete="username" required defaultValue="curator"/></label><label>Password<input name="password" type="password" autoComplete="current-password" required/></label><button disabled={busy}>Sign in →</button></form>{error && <p role="alert" className="error">{error}</p>}</div><aside><div className="plant">✳</div><h2>From scattered records<br />to reviewed knowledge.</h2><p>Keep the source. Question the suggestion.<br />Make the final call.</p></aside></main>;
    return <div className="app"><aside className="sidebar"><div className="brand">g<span>genesis</span></div><p className="eyebrow">CURATION WORKSPACE</p><nav><button className={view === 'inbox' ? 'current' : ''} onClick={() => setView('inbox')}>✓ My to-do list</button><button className={view === 'review' ? 'current' : ''} onClick={() => setView('review')}>◫ My datasets <small>{subjects.length}</small></button><button className={view === 'import' ? 'current' : ''} onClick={() => setView('import')}>＋ Add dataset</button><button className={view === 'settings' ? 'current' : ''} onClick={() => setView('settings')}>⚙ Settings</button></nav><div className="sidebar-bottom"><span className="avatar">{actor[0].toUpperCase()}</span><div>{actor}<small>Human reviewer</small></div><button aria-label="Sign out" onClick={() => void work(async () => { await api('/logout', {}); setActor(''); })}>↗</button></div></aside>
 <main className="workspace"><header><div><p className="eyebrow">GENESIS / {view === 'import' ? 'IMPORT' : view === 'settings' ? 'CONFIGURATION' : 'METADATA'}</p><h1>{view === 'import' ? 'Add a dataset' : view === 'settings' ? 'Models & skills' : view === 'inbox' ? 'Your review to-do list' : 'Record details'}</h1></div><div className="header-actions"><span className="pill">● Human approval required</span><a className="button secondary" href="/api/export">Download reviewed metadata ↓</a></div></header>
 {error && <div role="alert" className="error banner">{error}<button onClick={() => setError('')}>×</button></div>}{notice && <div role="status" className="notice banner">{notice}</div>}

 {trackedJob && currentJob && <div className="job-banner" role="status"><span>{jobRunning ? 'Preparing your results… You can keep reviewing.' : currentJob.status === 'SUCCEEDED' ? 'Your AI results are ready.' : `AI task ${currentJob.status.toLowerCase()}. ${currentJob.error || 'You can try again from the record.'}`}</span>{currentJob.status === 'SUCCEEDED' && <button onClick={() => void work(async () => { await open(trackedJob.subject); setTrackedJob(null); })}>View {currentJob.kind === 'chat' ? 'answer' : 'suggestions'} →</button>}{!jobRunning && <button className="text-button" aria-label="Dismiss AI task notification" onClick={() => setTrackedJob(null)}>Dismiss</button>}</div>}
 {view === 'inbox' ? <Inbox api={api} provider={provider} skill={skill} add={() => setView('import')} inspect={(id, field) => void work(() => open(id, field))}/> : view === 'import' ? <section className="panel import"><p className="eyebrow">01 / ADD SOURCES</p><h2>Paste a study ID or upload a table</h2><p>Preview the records before importing. Original metadata is preserved; suggestions never become accepted values without your review.</p><div className="import-options"><label className="file-drop">Upload CSV, TSV or JSON<input type="file" accept=".csv,.tsv,.json" onChange={e => { const f = e.target.files?.[0]; if (f)
            void work(() => loadFile(f)); }}/><small>Up to 2 MiB · 1,000 records per import</small></label><form onSubmit={e => { e.preventDefault(); void work(async () => { const j = await api<{
            id: string;
        }>('/jobs', { kind: 'accession', payload: { accession } }); setAccessionJob(j.id); setPreview([]); setNotice('Fetching public metadata. No sequencing data will be downloaded.'); await refresh(); }); }}><label>GEO / ENA / SRA accession<input value={accession} onChange={e => setAccession(e.target.value)} placeholder="GSE… or SRP…" required/></label><button className="secondary" disabled={busy}>Fetch preview</button></form></div>
 {preview.length > 0 && <><div className="section-title"><h3>{preview.length} records ready to inspect</h3><span>{filename}</span></div><div className="table-scroll"><table><thead><tr>{Object.keys(preview[0]).slice(0, 7).map(k => <th key={k}>{nice(k)}</th>)}</tr></thead><tbody>{preview.slice(0, 6).map((r, i) => <tr key={i}>{Object.keys(preview[0]).slice(0, 7).map(k => <td key={k}>{display(r[k])}</td>)}</tr>)}</tbody></table></div><div className="form-grid"><label>Dataset name<input value={namespace} onChange={e => setNamespace(e.target.value)}/></label><label>Column that identifies each record<select value={idColumn} onChange={e => setIdColumn(e.target.value)}>{Object.keys(preview[0]).map(k => <option key={k}>{k}</option>)}</select></label><label>Who can process this data?<select value={classification} onChange={e => setClassification(e.target.value)}><option value="local-only">Local model only</option><option value="internal">Internal — approved providers only</option><option value="public">Public — may use external AI</option></select></label></div><p className="muted">Adding these records starts AI preparation with an available provider permitted by the selected sharing classification. Suggestions still need your approval.</p><button disabled={busy} onClick={() => void work(async () => { const r = await api<{
            subjects: string[];
        }>('/imports/commit', { text, filename, namespace, id_column: idColumn, classification, job: accessionJob || undefined }); setPreview([]); setActive(null); await refresh(); setView('inbox');
        const eligible = providers.find(p => p.mode !== 'disabled' && !p.external && p.allowed_classifications.includes(classification)) || providers.find(p => p.mode !== 'disabled' && p.allowed_classifications.includes(classification));
        if (eligible) { setProvider(eligible.id); await api('/prepare', {subjects:r.subjects,provider:eligible.id,skill}); setNotice(`Added ${r.subjects.length} records. AI is preparing your to-do list; you can leave and return later.`); }
        else setNotice(`Added ${r.subjects.length} records. No AI provider is available for this classification. Source-extracted tasks are ready in your inbox.`); })}>Add {preview.length} records and let AI prepare →</button></>}
 </section> : view === 'settings' ? <section className="settings"><div className="panel"><p className="eyebrow">INFERENCE</p><h2>Choose the model, keep the evidence</h2><p>Provider credentials and deployment are managed over SSH. Private records cannot silently fall back to an external API.</p>{providers.map(p => <div className="provider-card" key={p.id}><div><h3>{p.id}</h3><p>{p.model}</p><small>{p.mode === 'mock' ? 'Synthetic testing provider' : p.external ? 'External API' : 'Private endpoint'} · {p.allowed_classifications.join(', ')}</small></div><button className="secondary" onClick={() => void work(async () => { await api('/jobs', { kind: 'probe', payload: { provider: p.id } }); setNotice('Provider check queued. See recent activity.'); await refresh(); })}>Check connection</button></div>)}</div><div className="panel"><p className="eyebrow">GUIDANCE</p><h2>Versioned curation skills</h2><p>Skills guide interpretation. Every proposed fact still needs evidence from the selected record.</p>{skillList.map(s => <article key={s.id}><h3>{s.title}</h3><code>{s.sha256.slice(0, 16)}</code></article>)}</div></section> : <>
 <button className="back-to-inbox" onClick={() => setView('inbox')}>← Back to my to-do list</button><div className="stats"><article><span>Records</span><strong>{subjects.length}</strong><small>Source-backed metadata</small></article><article><span>Need review</span><strong>{subjects.filter(s => s.pending > 0).length}</strong><small>Awaiting a human decision</small></article><article><span>Uncertain fields</span><strong>{subjects.reduce((n, s) => n + s.uncertain, 0)}</strong><small>Unknown or conflicting evidence</small></article></div>
 <div className="review-layout"><section className="panel record-list"><div className="section-title"><h2>Review queue</h2><button className="text-button" onClick={() => setView('import')}>＋ Import</button></div><input aria-label="Search records" placeholder="Find a record…" value={query} onChange={e => setQuery(e.target.value)}/>{subjects.filter(s => s.name.toLowerCase().includes(query.toLowerCase())).map(s => <button className={'record ' + (active?.id === s.id ? 'selected' : '')} key={s.id} onClick={() => void work(() => open(s.id))}><strong>{s.name}</strong><span>{s.classification} <small>{s.pending ? 'Needs review' : 'Reviewed'}</small></span></button>)}{!subjects.length && <div className="empty"><h3>Start with your first dataset.</h3><p>Paste a public study ID or upload a metadata table. We’ll help you review it one field at a time.</p><button onClick={() => setView('import')}>Add a dataset →</button></div>}</section>
 <section className="panel detail">{!active ? <div className="empty"><span className="empty-symbol">◈</span><h2>Ready to start?</h2><p>Open a dataset, check the source, and approve the fields you can verify.</p>{subjects.length > 0 && <button onClick={() => void work(() => open((subjects.find(s => s.pending > 0) || subjects[0]).id))}>Start reviewing →</button>}</div> : <><div className="section-title"><div><p className="eyebrow">{active.classification} / {active.kind}</p><h2>{active.name}</h2></div><button className="secondary" onClick={() => void work(async () => { setActive(await api<Subject>('/subjects/' + active.id)); setNotice('Record refreshed.'); })}>Refresh</button></div><div className="review-progress"><span>{reviewed} of {active.fields.length} fields reviewed · {approved} approved</span><progress aria-label="Fields reviewed" value={reviewed} max={active.fields.length}/><small>Unresolved fields stay out of accepted metadata. Processing and QC are separate steps.</small></div>
 <div className="assist-bar"><div><strong>Need help interpreting the source?</strong><p>AI can suggest values. You make the final decision.</p></div><button disabled={busy || !provider || !!jobRunning} onClick={() => void work(() => enqueue('propose'))}>{jobRunning ? 'AI is working…' : reviewed ? 'Re-analyze and restart review' : 'Suggest with AI'}</button></div>
 {!provider && <p className="muted">AI is unavailable for this record. You can still review its source evidence.</p>}
 <details className="advanced"><summary>AI settings and guidance</summary><div className="model-bar"><label>Model<select value={provider} onChange={e => setProvider(e.target.value)}>{providers.filter(p => p.mode !== 'disabled' && p.allowed_classifications.includes(active.classification)).map(p => <option key={p.id} value={p.id}>{p.id} · {p.model}</option>)}</select></label><label>Skill<select value={skill} onChange={e => setSkill(e.target.value)}>{skillList.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}<option value="none">No skill (comparison)</option></select></label></div><label>What should AI look at again?<textarea value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="For example: check whether ‘root’ describes this sample or another sample in the study."/></label><small>New suggestions replace the current proposal and require a fresh review.</small></details>


 {selectedField && <section className="decision"><div className="section-title"><div><p className="eyebrow">CHECK THIS FIELD</p><h2>{nice(selected)}</h2></div><label className="checkbox"><input type="checkbox" checked={edit} onChange={e => setEdit(e.target.checked)}/>Correct this value</label></div><div className="suggested-value"><span>Suggested value</span><strong>{display(selectedField.value)}</strong><small>{selectedField.status === 'unknown' ? 'The source does not provide this information.' : selectedField.status === 'conflicting' ? 'Sources disagree. Check the evidence before correcting this field.' : 'Compare this suggestion with the source below.'}</small></div><p>{selectedField.rationale}</p>{active.reviews[selected] && <p className="notice">Last review by {active.reviews[selected].actor}: {active.reviews[selected].reason}</p>}{selectedField.evidence.map((c, i) => <blockquote key={i}>{c.quote}<footer>{c.locator} · source {c.source_version.slice(0, 10)}</footer></blockquote>)}{!selectedField.evidence.length && <p className="muted">No supporting source yet. Choose “Needs information,” or add evidence below.</p>}
 {edit && <div className="correction"><label>Corrected value<input value={value} onChange={e => setValue(e.target.value)}/></label><label>Supporting source<select value={source} onChange={e => { setSource(e.target.value); setLocator(''); setQuote(''); }}>{active.sources.map(s => <option key={s.version} value={s.version}>{s.data.location}</option>)}</select></label><label>Choose the supporting excerpt<select value={excerpts.findIndex(item => item.locator === locator && item.quote === quote)} onChange={e => { const item = excerpts[Number(e.target.value)]; if (item) { setLocator(item.locator); setQuote(item.quote); } }}><option value={-1}>Select text from the source…</option>{excerpts.map((item, i) => <option key={item.locator} value={i}>{item.locator}: {item.quote.slice(0, 180)}</option>)}</select></label>{quote && <blockquote>{quote}</blockquote>}<details><summary>Advanced: enter an exact citation</summary><label>Location in source<input value={locator} onChange={e => setLocator(e.target.value)} placeholder="/tissue or line:2"/></label><label>Exact supporting quote<textarea value={quote} onChange={e => setQuote(e.target.value)}/></label></details></div>}
 {['unknown', 'conflicting'].includes(selectedField.status) && <button className="text-button" onClick={() => { const panel = document.getElementById('source-evidence') as HTMLDetailsElement | null; if (panel) { panel.open = true; panel.scrollIntoView({behavior: 'smooth'}); panel.querySelector<HTMLInputElement>('input[name="label"]')?.focus({preventScroll: true}); } }}>＋ Add missing evidence</button>}
 <label>Why this decision?<textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Add a short note, or choose one below."/></label><div className="reason-shortcuts"><button className="secondary" onClick={() => setReason(edit ? 'The corrected value matches the cited source.' : 'The suggested value matches the cited source.')}>Matches the source</button><button className="secondary" onClick={() => setReason('The source does not provide enough information to decide.')}>Not enough information</button></div><div className="decision-actions"><button disabled={busy || !reason.trim() || (!edit && ['unknown', 'conflicting'].includes(selectedField.status)) || (edit && (!value.trim() || !locator || !quote.trim()))} onClick={() => void work(() => decide('approve'))}>Approve and continue →</button><button className="secondary" disabled={busy || !reason.trim()} onClick={() => void work(() => decide('reject'))}>Reject</button><button className="secondary" disabled={busy || !reason.trim()} onClick={() => void work(() => decide('request-info'))}>Needs information</button><button className="text-button" disabled={busy} onClick={() => { const fields = orderedFields(active); const start = fields.findIndex(f => f.field === selected) + 1; const next = [...fields.slice(start), ...fields.slice(0, start)].find(f => f.field !== selected && !active.reviews[f.field]); if (next) choose(next); else setSelected(''); setNotice('Skipped for now. No decision was saved.'); }}>Skip for now</button></div>{active.reviews[selected]?.action === 'approve' && <button className="text-button promote" onClick={() => void work(async () => { setActive(await api<Subject>('/subjects/' + active.id + '/examples', { version: active.version, field: selected })); setNotice('Promoted as a versioned example for the same organism and tissue.'); })}>＋ Use this reviewed decision as a future example</button>}</section>}
 {!selectedField && <div className="review-complete"><h3>{reviewed === active.fields.length ? 'You’ve reviewed every field in this record.' : 'Choose a field to continue.'}</h3><p>{approved} approved · {active.fields.length - approved} unresolved. You can revisit any field below.</p><button className="secondary" disabled={!subjects.some(s => s.id !== active.id && s.pending > 0)} onClick={() => void work(async () => { const next = subjects.find(s => s.id !== active.id && s.pending > 0); if (next) await open(next.id); })}>Next dataset →</button></div>}
 <details className="all-fields"><summary>See all {active.fields.length} fields or revisit a decision</summary> <div className="section-title"><h3>Field-by-field evidence</h3><select aria-label="Filter fields" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All fields</option><option value="uncertain">Uncertain only</option><option value="known">Source-supported</option></select></div><div className="field-table"><table><thead><tr><th>Field</th><th>Current</th><th>Proposed</th><th>Status</th></tr></thead><tbody>{active.fields.filter(f => filter === 'all' || (filter === 'uncertain' ? ['unknown', 'conflicting'].includes(f.status) : f.status === 'known')).map(f => <tr key={f.field} className={selected === f.field ? 'selected' : ''}><td><button className="text-button" onClick={() => choose(f)}>{nice(f.field)}</button></td><td>{display(active.metadata[f.field])}</td><td>{display(f.value)}</td><td><span className={'tag ' + (active.reviews[f.field]?.action === 'approve' ? 'approved' : f.status)}>{active.reviews[f.field]?.action === 'approve' ? 'Approved' : active.reviews[f.field]?.action === 'reject' ? 'Rejected' : active.reviews[f.field]?.action === 'request-info' ? 'Needs information' : nice(f.status)}</span></td></tr>)}</tbody></table></div></details>
 <details className="sources" id="source-evidence"><summary>Source evidence ({active.sources.length})</summary>{active.sources.map(s => <article key={s.version}><strong>{s.data.location}</strong><pre>{s.data.snapshot}</pre></article>)}<EvidenceForm active={active} onSave={s => setActive(s)} work={work}/></details><details><summary onClick={() => { if (!history)
                void work(async () => setHistory(await api<unknown[]>('/subjects/' + active.id + '/history'))); }}>Version and review history</summary><pre>{history ? JSON.stringify(history, null, 2) : 'Open to load history.'}</pre></details>
 <section className="conversation"><p className="eyebrow">ASK ABOUT THIS RECORD</p><h3>Work through the uncertain parts</h3>{active.messages.map((m, i) => <div key={i} className="message"><strong>You</strong><p>{m.question}</p><strong>Assistant · for review</strong><p>{m.answer}</p>{m.citations.map((c, j) => <blockquote key={j}>{c.quote}<footer>{c.locator}</footer></blockquote>)}</div>)}<form onSubmit={e => { e.preventDefault(); void work(() => enqueue('chat')); }}><label className="sr-only" htmlFor="question">Question about this record</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Which evidence supports the tissue assignment?" required/><button disabled={busy || !provider || !!jobRunning}>Ask →</button></form><small>Answers inform your review. They cannot approve or change metadata.</small></section>
 </>}</section></div></>}
 <details className="activity"><summary>Background activity ({jobs.filter(j => ['QUEUED', 'RUNNING'].includes(j.status)).length} running or queued)</summary><div className="section-title"><h3>Recent activity</h3><span>Jobs continue when you close this page</span></div>{jobs.slice(0, 6).map(j => <div className="job" key={j.id}><span className={'dot ' + j.status.toLowerCase()}/><strong>{nice(j.kind)}</strong><span>{j.status}</span>{j.error && <small className="error-text">{j.error}</small>}{['QUEUED', 'RUNNING'].includes(j.status) && <button className="text-button" onClick={() => void work(async () => { await api('/jobs/' + j.id + '/cancel', {}); await refresh(); })}>Cancel</button>}{j.status === 'SUCCEEDED' && j.kind === 'probe' && <button className="text-button" onClick={() => void work(async () => { const detail = await api<Job>('/jobs/' + j.id); setNotice(JSON.stringify(detail.result)); })}>View check</button>}</div>)}{!jobs.length && <p className="muted">No background jobs yet.</p>}</details><footer className="app-footer">Genesis curation · Original sources preserved · Every accepted change is reviewed <div><a href="/api/export?format=csv">CSV</a><a href="/api/export?format=tsv">TSV</a><a href="/api/export">JSON + provenance</a></div></footer></main></div>;
}
function EvidenceForm({ active, onSave, work }: {
    active: Subject;
    onSave: (s: Subject) => void;
    work: (fn: () => Promise<void>) => Promise<void>;
}) { return <form onSubmit={e => { e.preventDefault(); const form = e.currentTarget, d = new FormData(form); void work(async () => { const s = await api<Subject>('/subjects/' + active.id + '/evidence', { version: active.version, text: d.get('text'), label: d.get('label'), classification: d.get('classification') }); onSave(s); form.reset(); }); }}><h4>Add supporting evidence</h4><label>Source description<input name="label" required placeholder="Methods excerpt, sample record, or curator observation"/></label><label>Source text<textarea name="text" required/></label><label>Evidence classification<select name="classification" defaultValue={active.classification}><option value="local-only">Local model only</option><option value="internal">Internal — approved providers only</option><option value="public">Public — may use external AI</option></select></label><button className="secondary">Attach evidence</button><small>New evidence invalidates existing field reviews until reassessed.</small></form>; }
createRoot(document.getElementById('root')!).render(<App />);
