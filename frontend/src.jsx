import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

function useQuery(path, key, revision) {
  const [result, setResult] = useState({});
  useEffect(() => {
    if (!key || !path) return;
    const abort = new AbortController();
    fetch(path, {headers: {Authorization: 'Bearer ' + key}, signal: abort.signal, cache: 'no-store'})
      .then(async response => {
        if (!response.ok) throw Error(response.status === 401 ? 'Application key rejected.' : 'Request failed (' + response.status + ').');
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

function App() {
  const [token, connect] = useState('');
  return <><header><span className="mark" aria-hidden="true">A</span> AgentShield</header><main>
    <h1>Run explorer</h1>{token ? <><button className="disconnect" onClick={()=>connect('')}>Disconnect</button>
      <Workspace token={token}/></> :
      <form onSubmit={e=>{e.preventDefault(); connect(e.currentTarget.elements.key.value.trim());}}>
        <label>Application key<input type="password" name="key" autoComplete="off" required/></label>
        <button className="primary">Connect</button><p>The key stays in memory until you disconnect or reload.</p></form>}</main></>;
}

createRoot(document.getElementById('root')).render(<App/>);
