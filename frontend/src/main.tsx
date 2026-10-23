import React, { useEffect, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
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
const nice = (s: string) => s.replaceAll('_', ' ');
const display = (v: unknown) => v === null || v === undefined ? 'Not recorded' : String(v);
function App() {
    const [actor, setActor] = useState(''), [boot, setBoot] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
    const [subjects, setSubjects] = useState<Subject[]>([]), [active, setActive] = useState<Subject | null>(null), [jobs, setJobs] = useState<Job[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]), [provider, setProvider] = useState(''), [skillList, setSkillList] = useState<{
        id: string;
        title: string;
        sha256: string;
    }[]>([]), [skill, setSkill] = useState('plant-metadata');
    const [view, setView] = useState('review'), [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [selected, setSelected] = useState('');
    const [reason, setReason] = useState(''), [edit, setEdit] = useState(false), [value, setValue] = useState(''), [source, setSource] = useState(''), [locator, setLocator] = useState(''), [quote, setQuote] = useState('');
    const [question, setQuestion] = useState(''), [feedback, setFeedback] = useState(''), [busy, setBusy] = useState(false), [history, setHistory] = useState<unknown[] | null>(null);
    const [filename, setFilename] = useState(''), [text, setText] = useState(''), [preview, setPreview] = useState<Record<string, unknown>[]>([]), [idColumn, setIdColumn] = useState(''), [namespace, setNamespace] = useState(''), [classification, setClassification] = useState('local-only'), [accession, setAccession] = useState(''), [accessionJob, setAccessionJob] = useState('');
    const loadedAccession = useRef('');
    const selectedField = active?.fields.find(f => f.field === selected);
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
    async function open(id: string) { const s = await api<Subject>('/subjects/' + id); setActive(s); setSelected(''); setHistory(null); setView('review'); }
    useEffect(() => { api<{
        actor: string;
        csrf: string;
    }>('/session').then(s => { csrf = s.csrf; setActor(s.actor); }).catch(() => { }).finally(() => setBoot(false)); }, []);
    useEffect(() => {
        if (!actor)
            return;
        void work(async () => { await refresh(); const [p, s] = await Promise.all([api<Provider[]>('/providers'), api<typeof skillList>('/skills')]); setProviders(p); setProvider(p.find(x => x.id === 'local')?.id || p[0]?.id || ''); setSkillList(s); });
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
    function choose(f: Field) { setSelected(f.field); setReason(''); setEdit(false); setValue(f.value === null ? '' : String(f.value)); setSource(f.evidence[0]?.source_version || active?.sources[0]?.version || ''); setLocator(f.evidence[0]?.locator || 'line:1'); setQuote(f.evidence[0]?.quote || ''); }
    async function enqueue(kind: string) { if (!active)
        return; await api('/jobs', { kind, payload: { subject: active.id, version: active.version, provider, ...(kind === 'chat' ? { question } : { skill, feedback }) } }); setQuestion(''); setNotice('Queued. You can keep browsing; refresh this record when the job finishes.'); await refresh(); }
    async function decide(action: string) {
        if (!active || !selectedField)
            return;
        const correction = edit ? { value, status: 'known', vocabulary_id: null, rationale: reason, evidence: [{ source_version: source, locator, quote }] } : undefined;
        const s = await api<Subject>('/subjects/' + active.id + '/decisions', { version: active.version, field: selected, action, reason, correction });
        setActive(s);
        setReason('');
        setEdit(false);
        await refresh();
        setNotice(action === 'approve' ? 'Approved and recorded.' : 'Decision recorded.');
    }
    async function loadFile(file: File) { if (file.size > 2 * 1024 * 1024)
        throw new Error('Split uploads larger than 2 MiB.'); const t = await file.text(); setFilename(file.name); setText(t); setAccessionJob(''); const p = await api<{
        rows: Record<string, unknown>[];
        columns: string[];
    }>('/imports/preview', { filename: file.name, text: t }); setPreview(p.rows); setIdColumn(p.columns[0]); setNamespace(file.name.replace(/\.[^.]+$/, '')); }
    if (boot)
        return <main className="login"><p>Opening Genesis…</p></main>;
    if (!actor)
        return <main className="login"><div className="login-card"><div className="brand">g<span>genesis</span></div><p className="eyebrow">METADATA CURATION</p><h1>Evidence first.<br />Human decisions.</h1><p>Sign in to review plant study metadata, trace proposals to their sources, and curate with confidence.</p><form onSubmit={e => { e.preventDefault(); const d = new FormData(e.currentTarget); void work(async () => { const s = await api<{
            actor: string;
            csrf: string;
        }>('/login', { username: d.get('username'), password: d.get('password') }); csrf = s.csrf; setActor(s.actor); }); }}><label>Curator<input name="username" autoComplete="username" required defaultValue="curator"/></label><label>Password<input name="password" type="password" autoComplete="current-password" required/></label><button disabled={busy}>Sign in →</button></form>{error && <p role="alert" className="error">{error}</p>}</div><aside><div className="plant">✳</div><h2>From scattered records<br />to reviewed knowledge.</h2><p>Keep the source. Question the suggestion.<br />Make the final call.</p></aside></main>;
    return <div className="app"><aside className="sidebar"><div className="brand">g<span>genesis</span></div><p className="eyebrow">CURATION WORKSPACE</p><nav><button className={view === 'review' ? 'current' : ''} onClick={() => setView('review')}>◫ Review queue <small>{subjects.length}</small></button><button className={view === 'import' ? 'current' : ''} onClick={() => setView('import')}>＋ Import metadata</button><button className={view === 'settings' ? 'current' : ''} onClick={() => setView('settings')}>⚙ Models & skills</button></nav><div className="sidebar-bottom"><span className="avatar">{actor[0].toUpperCase()}</span><div>{actor}<small>Human reviewer</small></div><button aria-label="Sign out" onClick={() => void work(async () => { await api('/logout', {}); setActor(''); })}>↗</button></div></aside>
 <main className="workspace"><header><div><p className="eyebrow">GENESIS / {view === 'import' ? 'IMPORT' : view === 'settings' ? 'CONFIGURATION' : 'METADATA'}</p><h1>{view === 'import' ? 'Bring your evidence' : view === 'settings' ? 'Models & skills' : 'Metadata review'}</h1></div><div className="header-actions"><span className="pill">● Human approval required</span><a className="button secondary" href="/api/export">Export approved ↓</a></div></header>
 {error && <div role="alert" className="error banner">{error}<button onClick={() => setError('')}>×</button></div>}{notice && <div role="status" className="notice banner">{notice}</div>}
 {view === 'import' ? <section className="panel import"><p className="eyebrow">01 / ADD SOURCES</p><h2>Start with a table or an accession</h2><p>Preview the records before importing. Original metadata is preserved; suggestions never become accepted values without your review.</p><div className="import-options"><label className="file-drop">Upload CSV, TSV or JSON<input type="file" accept=".csv,.tsv,.json" onChange={e => { const f = e.target.files?.[0]; if (f)
            void work(() => loadFile(f)); }}/><small>Up to 2 MiB · 1,000 records per import</small></label><form onSubmit={e => { e.preventDefault(); void work(async () => { const j = await api<{
            id: string;
        }>('/jobs', { kind: 'accession', payload: { accession } }); setAccessionJob(j.id); setPreview([]); setNotice('Fetching public metadata. No sequencing data will be downloaded.'); await refresh(); }); }}><label>GEO / ENA / SRA accession<input value={accession} onChange={e => setAccession(e.target.value)} placeholder="GSE… or SRP…" required/></label><button className="secondary" disabled={busy}>Fetch preview</button></form></div>
 {preview.length > 0 && <><div className="section-title"><h3>{preview.length} records ready to inspect</h3><span>{filename}</span></div><div className="table-scroll"><table><thead><tr>{Object.keys(preview[0]).slice(0, 7).map(k => <th key={k}>{nice(k)}</th>)}</tr></thead><tbody>{preview.slice(0, 6).map((r, i) => <tr key={i}>{Object.keys(preview[0]).slice(0, 7).map(k => <td key={k}>{display(r[k])}</td>)}</tr>)}</tbody></table></div><div className="form-grid"><label>Source namespace<input value={namespace} onChange={e => setNamespace(e.target.value)}/></label><label>Unique record identifier<select value={idColumn} onChange={e => setIdColumn(e.target.value)}>{Object.keys(preview[0]).map(k => <option key={k}>{k}</option>)}</select></label><label>Data classification<select value={classification} onChange={e => setClassification(e.target.value)}><option value="local-only">Local only</option><option value="internal">Internal</option><option value="public">Public</option></select></label></div><button disabled={busy} onClick={() => void work(async () => { const r = await api<{
            subjects: string[];
        }>('/imports/commit', { text, filename, namespace, id_column: idColumn, classification, job: accessionJob || undefined }); setPreview([]); await refresh(); await open(r.subjects[0]); setNotice(`Imported ${r.subjects.length} records. Review the extracted fields or request model assistance.`); })}>Import {preview.length} records →</button></>}
 </section> : view === 'settings' ? <section className="settings"><div className="panel"><p className="eyebrow">INFERENCE</p><h2>Choose the model, keep the evidence</h2><p>Provider credentials and deployment are managed over SSH. Private records cannot silently fall back to an external API.</p>{providers.map(p => <div className="provider-card" key={p.id}><div><h3>{p.id}</h3><p>{p.model}</p><small>{p.mode === 'mock' ? 'Synthetic testing provider' : p.external ? 'External API' : 'Private endpoint'} · {p.allowed_classifications.join(', ')}</small></div><button className="secondary" onClick={() => void work(async () => { await api('/jobs', { kind: 'probe', payload: { provider: p.id } }); setNotice('Provider check queued. See recent activity.'); await refresh(); })}>Check connection</button></div>)}</div><div className="panel"><p className="eyebrow">GUIDANCE</p><h2>Versioned curation skills</h2><p>Skills guide interpretation. Every proposed fact still needs evidence from the selected record.</p>{skillList.map(s => <article key={s.id}><h3>{s.title}</h3><code>{s.sha256.slice(0, 16)}</code></article>)}</div></section> : <>
 <div className="stats"><article><span>Records</span><strong>{subjects.length}</strong><small>Source-backed metadata</small></article><article><span>Need review</span><strong>{subjects.filter(s => s.pending > 0).length}</strong><small>Awaiting a human decision</small></article><article><span>Uncertain fields</span><strong>{subjects.reduce((n, s) => n + s.uncertain, 0)}</strong><small>Unknown or conflicting evidence</small></article></div>
 <div className="review-layout"><section className="panel record-list"><div className="section-title"><h2>Review queue</h2><button className="text-button" onClick={() => setView('import')}>＋ Import</button></div><input aria-label="Search records" placeholder="Find a record…" value={query} onChange={e => setQuery(e.target.value)}/>{subjects.filter(s => s.name.toLowerCase().includes(query.toLowerCase())).map(s => <button className={'record ' + (active?.id === s.id ? 'selected' : '')} key={s.id} onClick={() => void work(() => open(s.id))}><strong>{s.name}</strong><span>{s.classification} <small>{s.pending ? 'Needs review' : 'Reviewed'}</small></span></button>)}{!subjects.length && <div className="empty"><h3>Your review queue starts here.</h3><p>Import metadata to see source-backed suggestions.</p><button onClick={() => setView('import')}>Import metadata</button></div>}</section>
 <section className="panel detail">{!active ? <div className="empty"><span className="empty-symbol">◈</span><h2>Make the evidence visible.</h2><p>Select a record to compare metadata, inspect sources and record your decisions.</p></div> : <><div className="section-title"><div><p className="eyebrow">{active.classification} / {active.kind}</p><h2>{active.name}</h2></div><button className="secondary" onClick={() => void work(async () => { setActive(await api<Subject>('/subjects/' + active.id)); setNotice('Record refreshed.'); })}>Refresh</button></div><div className="model-bar"><label>Model<select value={provider} onChange={e => setProvider(e.target.value)}>{providers.filter(p => p.mode !== 'disabled' && p.allowed_classifications.includes(active.classification)).map(p => <option key={p.id} value={p.id}>{p.id} · {p.model}</option>)}</select></label><label>Skill<select value={skill} onChange={e => setSkill(e.target.value)}>{skillList.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}<option value="none">No skill (comparison)</option></select></label><button disabled={busy || !provider} onClick={() => void work(() => enqueue('propose'))}>Propose metadata</button></div><details><summary>Guidance for re-analysis</summary><textarea aria-label="Feedback for re-analysis" value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="Explain what needs another look. Attach new evidence below when needed."/></details>
 <div className="section-title"><h3>Field-by-field evidence</h3><select aria-label="Filter fields" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All fields</option><option value="uncertain">Uncertain only</option><option value="known">Source-supported</option></select></div><div className="field-table"><table><thead><tr><th>Field</th><th>Current</th><th>Proposed</th><th>Status</th></tr></thead><tbody>{active.fields.filter(f => filter === 'all' || (filter === 'uncertain' ? ['unknown', 'conflicting'].includes(f.status) : f.status === 'known')).map(f => <tr key={f.field} className={selected === f.field ? 'selected' : ''}><td><button className="text-button" onClick={() => choose(f)}>{nice(f.field)}</button></td><td>{display(active.metadata[f.field])}</td><td>{display(f.value)}</td><td><span className={'tag ' + (active.reviews[f.field]?.action === 'approve' ? 'approved' : f.status)}>{active.reviews[f.field]?.action === 'approve' ? 'Approved' : nice(f.status)}</span></td></tr>)}</tbody></table></div>
 {selectedField && <section className="decision"><div className="section-title"><h3>Review: {nice(selected)}</h3><label className="checkbox"><input type="checkbox" checked={edit} onChange={e => setEdit(e.target.checked)}/>Correct this value</label></div><p>{selectedField.rationale}</p>{selectedField.evidence.map((c, i) => <blockquote key={i}>{c.quote}<footer>{c.locator} · source {c.source_version.slice(0, 10)}</footer></blockquote>)}{!selectedField.evidence.length && <p className="muted">No supporting citation. Add evidence before approving a value.</p>}
 {edit && <div className="correction"><label>Corrected value<input value={value} onChange={e => setValue(e.target.value)}/></label><label>Supporting source<select value={source} onChange={e => setSource(e.target.value)}>{active.sources.map(s => <option key={s.version} value={s.version}>{s.data.location}</option>)}</select></label><label>Locator (JSON pointer or line number)<input value={locator} onChange={e => setLocator(e.target.value)} placeholder="/tissue or line:2"/></label><label>Exact supporting quote<textarea value={quote} onChange={e => setQuote(e.target.value)}/></label></div>}
 <label>Review reason<textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="What does the evidence establish? Why accept or reject this suggestion?"/></label><div className="decision-actions"><button disabled={busy || !reason.trim() || (!edit && ['unknown', 'conflicting'].includes(selectedField.status))} onClick={() => void work(() => decide('approve'))}>Approve {edit ? 'correction' : 'value'}</button><button className="secondary" disabled={busy || !reason.trim()} onClick={() => void work(() => decide('reject'))}>Reject</button><button className="secondary" disabled={busy || !reason.trim()} onClick={() => void work(() => decide('request-info'))}>Request information</button></div>{active.reviews[selected]?.action === 'approve' && <button className="text-button promote" onClick={() => void work(async () => { setActive(await api<Subject>('/subjects/' + active.id + '/examples', { version: active.version, field: selected })); setNotice('Promoted as a versioned example for the same organism and tissue.'); })}>＋ Use this reviewed decision as a future example</button>}</section>}
 <details className="sources"><summary>Source evidence ({active.sources.length})</summary>{active.sources.map(s => <article key={s.version}><strong>{s.data.location}</strong><pre>{s.data.snapshot}</pre></article>)}<EvidenceForm active={active} onSave={s => setActive(s)} work={work}/></details><details><summary onClick={() => { if (!history)
                void work(async () => setHistory(await api<unknown[]>('/subjects/' + active.id + '/history'))); }}>Version and review history</summary><pre>{history ? JSON.stringify(history, null, 2) : 'Open to load history.'}</pre></details>
 <section className="conversation"><p className="eyebrow">ASK ABOUT THIS RECORD</p><h3>Work through the uncertain parts</h3>{active.messages.map((m, i) => <div key={i} className="message"><strong>You</strong><p>{m.question}</p><strong>Assistant · for review</strong><p>{m.answer}</p>{m.citations.map((c, j) => <blockquote key={j}>{c.quote}<footer>{c.locator}</footer></blockquote>)}</div>)}<form onSubmit={e => { e.preventDefault(); void work(() => enqueue('chat')); }}><label className="sr-only" htmlFor="question">Question about this record</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Which evidence supports the tissue assignment?" required/><button disabled={busy || !provider}>Ask →</button></form><small>Answers inform your review. They cannot approve or change metadata.</small></section>
 </>}</section></div></>}
 <section className="activity"><div className="section-title"><h3>Recent activity</h3><span>Jobs continue when you close this page</span></div>{jobs.slice(0, 6).map(j => <div className="job" key={j.id}><span className={'dot ' + j.status.toLowerCase()}/><strong>{nice(j.kind)}</strong><span>{j.status}</span>{j.error && <small className="error-text">{j.error}</small>}{['QUEUED', 'RUNNING'].includes(j.status) && <button className="text-button" onClick={() => void work(async () => { await api('/jobs/' + j.id + '/cancel', {}); await refresh(); })}>Cancel</button>}{j.status === 'SUCCEEDED' && j.kind === 'probe' && <button className="text-button" onClick={() => void work(async () => { const detail = await api<Job>('/jobs/' + j.id); setNotice(JSON.stringify(detail.result)); })}>View check</button>}</div>)}{!jobs.length && <p className="muted">No background jobs yet.</p>}</section><footer className="app-footer">Genesis curation · Original sources preserved · Every accepted change is reviewed <div><a href="/api/export?format=csv">CSV</a><a href="/api/export?format=tsv">TSV</a><a href="/api/export">JSON + provenance</a></div></footer></main></div>;
}
function EvidenceForm({ active, onSave, work }: {
    active: Subject;
    onSave: (s: Subject) => void;
    work: (fn: () => Promise<void>) => Promise<void>;
}) { return <form onSubmit={e => { e.preventDefault(); const form = e.currentTarget, d = new FormData(form); void work(async () => { const s = await api<Subject>('/subjects/' + active.id + '/evidence', { version: active.version, text: d.get('text'), label: d.get('label'), classification: d.get('classification') }); onSave(s); form.reset(); }); }}><h4>Add supporting evidence</h4><label>Source description<input name="label" required placeholder="Methods excerpt, sample record, or curator observation"/></label><label>Source text<textarea name="text" required/></label><label>Evidence classification<select name="classification" defaultValue={active.classification}><option value="local-only">Local only</option><option value="internal">Internal</option><option value="public">Public</option></select></label><button className="secondary">Attach evidence</button><small>New evidence invalidates existing field reviews until reassessed.</small></form>; }
createRoot(document.getElementById('root')!).render(<App />);
