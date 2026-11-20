import React, {useEffect, useState} from 'react';

export default function Investigation({incidentId, token}) {
  const base = '/v1/incidents/' + incidentId;
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const headers = {Authorization:'Bearer ' + token};
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const res = await fetch(base+'/report', {headers, signal:controller.signal, cache:'no-store'});
        if (res.ok) setReport(await res.json());
        else if (res.status !== 404) setError('Could not load the investigation.');
      } catch {
        if (!controller.signal.aborted) setError('Collector unavailable.');
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    load();
    return () => controller.abort();
  }, [base, token]);
  async function investigate() {
    setBusy(true);
    setError('');
    try {
      const res = await fetch(base+'/investigate', {method:'POST', headers});
      if (!res.ok) {
        const messages = {409:'An investigation is already running.',429:'Investigation budget reached. Retry in a minute.',
          503:'Investigation interrupted. Retry to resume.'};
        throw Error(messages[res.status] || 'Investigation request failed.');
      }
      setReport(await res.json());
    } catch (e) {setError(e.message);}
    finally {setBusy(false);}
  }
  async function download() {
    try {
      const res = await fetch(base+'/report.md?investigation_id='+report.investigation_id, {headers});
      if (!res.ok) throw Error();
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'incident-'+report.investigation_id+'.md';
      link.click();
      setTimeout(()=>URL.revokeObjectURL(url), 1000);
    } catch {setError('Could not download the report.');}
  }
  return <section aria-label="Investigation">
    <h2>Investigation</h2>
    <button disabled={busy} onClick={investigate}>{busy?'Loading investigation…':report?'Review latest evidence':'Investigate incident'}</button>
    {error && <p role="alert">{error}</p>}
    {report && <><p><strong>{report.verdict.replaceAll('_',' ')}</strong></p>
      {report.findings.map((f,i)=><details key={i}><summary>{f.statement}</summary>
        <small>{f.role.replaceAll('_',' ')} · {f.confidence}</small>
        {f.evidence.map(e=><dl key={e.event_id}><dt>Event</dt><dd>{e.event_id}</dd>
          <dt>Trace</dt><dd>{e.trace_id}</dd><dt>Span</dt><dd>{e.span_id}</dd></dl>)}</details>)}
      {report.limitations.map(note=><p key={note}>{note}</p>)}<p>{report.next_step}</p>
      <button onClick={download}>Download Markdown</button></>}
  </section>;
}
