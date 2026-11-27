import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import Investigation from './Investigation';

function useQuery(path, key, revision) {
  const [result, setResult] = useState({});
  useEffect(() => {
    if (!key || !path) return;
    const abort = new AbortController();
    fetch(path, {headers: {Authorization: 'Bearer ' + key}, signal: abort.signal, cache: 'no-store'})
      .then(async response => {
        if (!response.ok) throw Error(response.status === 401 ? 'Key rejected for this access mode.' : 'Request failed (' + response.status + ').');
        return response.json();
      })
      .then(data => {if (!abort.signal.aborted) setResult({path, revision, data});})
      .catch(e => {if (!abort.signal.aborted) setResult({path, revision, error: e.message});});
    return () => abort.abort();
  }, [path, key, revision]);
  return result.path === path && result.revision === revision ? result : {};
}

function Timeline({events}) {
  const end = e => Date.parse(e.timestamp);
  const left = Math.min(...events.map(e => end(e) - (e.duration_ms || 0)));
  const width = Math.max(1, ...events.map(e => end(e) - left));
  return <ol className="timeline">{events.map(e => <li key={e.event_id}>
    <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
    <details><summary><strong>{e.operation}</strong> <span className={'badge '+e.status}>{e.status}</span>
      <small>{e.event_type.replaceAll('_',' ')} {e.duration_ms != null && ' · '+Math.round(e.duration_ms)+' ms'}</small>
      {e.duration_ms != null && <span className="track" aria-hidden="true"><i style={{
        left:Math.max(0,(end(e)-e.duration_ms-left)/width*100)+'%',width:(e.duration_ms/width*100)+'%'
      }}/></span>}
    </summary><dl>{Object.entries({Trace:e.trace_id, Span:e.span_id, Parent:e.parent_span_id || 'Root', Event:e.event_id,
      ...e.attributes}).map(([name,value]) => <React.Fragment key={name}><dt>{name}</dt><dd>{String(value)}</dd></React.Fragment>)}</dl></details>
  </li>)}</ol>;
}

function Pager({offset, size, next, move}) {
  return <nav aria-label="Pagination"><button disabled={!offset} onClick={()=>move(offset-size)}>Previous</button>
    <button disabled={!next} onClick={()=>move(offset+size)}>Next</button></nav>;
}

function Workspace({token}) {
  const [status, filter] = useState('');
  const [offset, page] = useState(0);
  const [eventOffset, eventPage] = useState(0);
  const [selected, select] = useState(null);
  const [revision, refresh] = useState(0);
  const runs = useQuery('/v1/runs?limit=21&offset='+offset+(status ? '&status='+status : ''), token, revision);
  const active = runs.data?.slice(0,20).find(r=>r.run_id===selected) || runs.data?.[0];
  const detail = useQuery(active && '/v1/runs/'+active.run_id+'?offset='+eventOffset, token, revision);
  function reset() {eventPage(0); select(null);}
  return <><section className="toolbar"><label>Observed status <select value={status} onChange={e=>{filter(e.target.value);page(0);reset();}}>
    <option value="">All runs</option><option value="ok">OK</option><option value="error">Error</option></select></label>
    <button onClick={()=>{refresh(revision+1);eventPage(0);}}>Refresh</button></section>
    <div className="panels"><section><h2>Recent runs</h2>
      {runs.error ? <p role="alert">{runs.error}</p> : !runs.data ? <p>Loading runs…</p> :
        !runs.data.length ? <p>No matching runs.</p> : runs.data.slice(0,20).map(r=>
          <button key={r.run_id} className={'run '+(active?.run_id===r.run_id?'chosen':'')} aria-pressed={active?.run_id===r.run_id}
            onClick={()=>{select(r.run_id);eventPage(0);}}><code>{r.run_id.slice(0,12)}</code>
            <span className={'badge '+r.status}>{r.status}</span><small>{new Date(r.last_event_at).toLocaleString()} · {r.event_count} events</small></button>)}
      <Pager offset={offset} size={20} next={runs.data?.length>20} move={n=>{page(n);reset();}}/></section>
      <section><h2>Span timeline</h2>{!active ? <p>Select a run to inspect its events.</p> : <>
        <code>{active.run_id}</code>{detail.error ? <p role="alert">{detail.error}</p> : !detail.data ? <p>Loading events…</p> :
        <><Timeline events={detail.data.events}/><Pager offset={eventOffset} size={100}
          next={eventOffset+100<detail.data.run.event_count} move={eventPage}/></>}</>}</section></div></>;
}

const categories = ['invalid_schema','timeout','tool_exception','verification_failed','escaped_claim','missing_citation',
  'invalid_citation','slow_span','retry_limit','execution_error'];

function Incidents({token, fleet=false}) {
  const [filters, setFilters] = useState({status:'', severity:'', category:''});
  const [offset, page] = useState(0);
  const [selected, select] = useState(null);
  const [evidenceOffset, evidencePage] = useState(0);
  const [revision, refresh] = useState(0);
  const params = new URLSearchParams({offset, limit:21});
  for (const [name,value] of Object.entries(filters)) if (value) params.set(name,value);
  const base = fleet ? '/v1/fleet/incidents' : '/v1/incidents';
  const apps = useQuery(fleet && '/v1/fleet/apps', token, revision);
  const list = useQuery(base+'?'+params, token, revision);
  const visible = list.data?.slice(0,20) || [];
  const active = visible.find(i=>i.incident_id===selected) || visible[0];
  const detail = useQuery(active && base+'/'+active.incident_id+'?offset='+evidenceOffset+
                          (fleet ? '&app_id='+encodeURIComponent(active.app_id) : ''),
                          token, revision);
  const choices = {status:['open','acknowledged','resolved'], severity:['medium','high'], category:categories};
  if (fleet) choices.app_id = apps.data || [];
  function reset() {select(null); evidencePage(0);}
  return <><section className="toolbar incident-filters">{Object.entries(choices).map(([name,options])=>
    <label key={name}>{name==='app_id'?'Application':name} <select value={filters[name] || ''} onChange={e=>{
      setFilters({...filters,[name]:e.target.value}); page(0); reset();
    }}><option value="">All</option>{options.map(value=><option key={value} value={value}>
      {value.replaceAll('_',' ')}</option>)}</select></label>)}
    <button onClick={()=>{refresh(revision+1); evidencePage(0);}}>Refresh</button></section>
    <div className="panels"><section><h2>Incidents</h2>
      {list.error ? <p role="alert">{list.error}</p> : !list.data ? <p>Loading incidents…</p> :
       !visible.length ? <p>No matching incidents.</p> : visible.map(i=>
        <button key={i.incident_id} className={'run '+(active===i?'chosen':'')} aria-pressed={active===i}
          onClick={()=>{select(i.incident_id); evidencePage(0);}}>
          <strong>{i.category.replaceAll('_',' ')}</strong><span className="badge error">{i.severity}</span>
          <small>{i.status} · {new Date(i.last_seen).toLocaleString()} · {i.app_id}</small></button>)}
      <Pager offset={offset} size={20} next={list.data?.length>20} move={n=>{page(n);reset();}}/>
    </section><section><h2>Incident evidence</h2>
      {!active ? <p>Select an incident to inspect its evidence.</p> : detail.error ? <p role="alert">{detail.error}</p> :
       !detail.data ? <p>Loading evidence…</p> : <>
        <dl><dt>Incident</dt><dd>{active.incident_id}</dd><dt>Run</dt><dd>{active.run_id}</dd></dl>
        <p>{detail.data.incident.evidence_count} matching events · {active.status}</p>
        {!fleet && <Investigation key={active.incident_id} incidentId={active.incident_id} token={token}/>}
        {detail.data.evidence.map(({event,reason})=><article key={event.event_id}>
          <strong>{reason}</strong><Timeline events={[event]}/></article>)}
        <Pager offset={evidenceOffset} size={100} next={evidenceOffset+100<detail.data.incident.evidence_count}
          move={evidencePage}/></>}
    </section></div></>;
}

const icons = {
  shield: ['M12 2 21 6v6c0 5-5 8-9 10-4-2-9-5-9-10V6z','m8 12 3 3 5-6'],
  grid: ['M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'],
  runs: ['M2 12h5l3-8 4 16 3-8h5'],
  incident: ['m12 3 10 18H2z','M12 9v4 M12 16v1'],
  lock: ['M5 10h14v11H5z','M8 10V7a4 4 0 0 1 8 0v3'],
  layers: ['m2 7 10-5 10 5-10 5z','m2 12 10 5 10-5 M2 17l10 5 10-5'],
  key: ['M14 14a6 6 0 1 0-4-4L3 17v4h4v-3h3z'],
  eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
  hidden: ['m3 3 18 18','M4 8l-2 4s4 7 10 7c2 0 3-.4 5-1.5 M9 5.5c6-2 11 3 13 6.5l-2 3'],
  arrow: ['M4 12h15m-6-6 6 6-6 6'],
  menu: ['M3 6h18 M3 12h18 M3 18h18']
};
function Icon({name,size=18}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icons[name].map(d=><path key={d} d={d}/>)}</svg>;
}

function AccessPanel({fleet,setFleet,connect}) {
  const [shown,show] = useState(false);
  const [pending,busy] = useState(false);
  const [error,report] = useState('');
  async function submit(e) {
    e.preventDefault();
    const key=e.currentTarget.elements.key.value.trim();
    if (!key) {report('Enter an access key to continue.');return;}
    busy(true);
    report('');
    try {
      const response=await fetch(fleet?'/v1/fleet/apps':'/v1/runs?limit=1', {headers:{Authorization:'Bearer '+key},cache:'no-store'});
      if (!response.ok) throw Error([401,403].includes(response.status)?'Key rejected. Check your key and selected access mode.':'Connection failed ('+response.status+'). Try again.');
      await response.json();
      connect(key);
    } catch (err) {report(err instanceof TypeError || err instanceof SyntaxError?'Unable to reach the AgentShield API. Check your connection and try again.':err.message);}
    finally {busy(false);}
  }
  return <div className="access-layout"><form className="access-form" onSubmit={submit}>
    <h2>Connect to workspace</h2>
    <fieldset disabled={pending}><legend>Access mode</legend><div className="mode-options">{['Application','Fleet viewer'].map((name,i)=><label key={name} className={'mode '+(fleet===Boolean(i)?'selected':'')}>
      <input type="radio" name="mode" checked={fleet===Boolean(i)} onChange={()=>{setFleet(Boolean(i));report('');}}/>
      <Icon name={i?'layers':'grid'}/><span>{name}</span><i aria-hidden="true"/></label>)}</div></fieldset>
    <label htmlFor="access-key">{fleet?'Fleet viewer key':'Application key'}</label><div className="key-field"><Icon name="key"/><input id="access-key" name="key" type={shown?'text':'password'} placeholder="Enter your access key" required autoComplete="off" disabled={pending} onChange={()=>report('')}/><button type="button" aria-label={shown?'Hide key':'Show key'} onClick={()=>show(!shown)}><Icon name={shown?'hidden':'eye'}/></button></div>
    {error && <p role="alert">{error}</p>}
    <button className="primary" disabled={pending}>{pending?'Connecting…':'Connect to workspace'}<Icon name="arrow"/></button>
    <p className="memory"><Icon name="lock" size={15}/>Your key stays in memory until you disconnect or reload.</p>
  </form><aside className="access-info"><div className="blueprint" aria-hidden="true"><div className="orbit outer"/><div className="orbit inner"/><div className="axis horizontal"/><div className="axis vertical"/><div className="beacon"><Icon name="shield" size={58}/></div><i className="node n1"/><i className="node n2"/><i className="node n3"/></div>
    <div className="info-copy"><h2>Workspace access</h2><h3><Icon name="runs"/>Application</h3><p>Explore runs, inspect span timelines, and investigate incidents for one application.</p><h3><Icon name="layers"/>Fleet viewer</h3><p>Review incidents across applications with read-only fleet access.</p></div></aside></div>;
}
function App() {
  const [token, connect] = useState('');
  const [view, setView] = useState('Runs');
  const [fleet, setFleet] = useState(false);
  const [menu,setMenu] = useState(false);
  const title=!token?'Workspace connection':fleet?'Fleet incidents':view==='Runs'?'Run explorer':'Incident explorer';
  const links=[['Connection','key'],['Runs','runs'],['Incidents','incident']];
  return <div className="shell"><aside className={'sidebar '+(menu?'expanded':'')}><div className="brand"><span><Icon name="shield" size={23}/></span>AgentShield<button className="mobile-menu" aria-label="Toggle navigation" aria-expanded={menu} onClick={()=>setMenu(!menu)}><Icon name="menu"/></button></div>
    <nav aria-label="Workspace navigation"><h3>Workspace</h3>{links.map(([name,icon])=>{
      const current=token?(fleet?'Incidents':view)===name:name==='Connection';
      const disabled=name==='Connection'?Boolean(token):!token || (fleet && name==='Runs');
      return <button key={name} disabled={disabled} aria-current={current?'page':undefined} onClick={()=>{if(name!=='Connection')setView(name);setMenu(false);}}><Icon name={icon}/>{name}{disabled && <Icon name="lock" size={13}/>}</button>;
    })}</nav><div className="side-foot"><Icon name="shield" size={15}/>AgentShield Console</div></aside>
    <div className="content"><header className="topbar"><div><Icon name="grid" size={15}/>Workspace <span>/</span> <b>{token?title:'Connection'}</b></div><span className={'connection '+(token?'online':'')}><i/>{token?'Connected':'Disconnected'}</span></header>
    <main><div className="page-heading"><div><h1>{title}</h1><p>{!token?'Connect your workspace to explore agent activity and investigate incidents.':view==='Runs'&&!fleet?'Inspect run activity and the events behind each trace.':'Review incidents and inspect their supporting evidence.'}</p></div>
    {token && <button onClick={()=>{connect('');setView('Runs');}}>Disconnect</button>}</div>
    {!token?<AccessPanel fleet={fleet} setFleet={setFleet} connect={connect}/>:!fleet && view==='Runs'?<Workspace token={token}/>:<Incidents token={token} fleet={fleet}/>}</main></div></div>;
}
createRoot(document.getElementById('root')).render(<App/>);