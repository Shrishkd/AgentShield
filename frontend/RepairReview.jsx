import React, {useEffect, useRef, useState} from 'react';

export default function RepairReview({report, token}) {
  const base = '/v1/incidents/' + report.incident_id;
  const version = '?investigation_id=' + report.investigation_id;
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const secret = useRef(null);
  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        const res = await fetch(base+'/proposal'+version, {headers:{Authorization:'Bearer '+token}, signal:abort.signal});
        if (res.ok) setReview(await res.json());
        else if (res.status !== 404) throw Error('Could not load proposal.');
      } catch (e) {if (!abort.signal.aborted) setError(e.message);}
      finally {if (!abort.signal.aborted) setBusy(false);}
    })();
    return () => abort.abort();
  }, [base,version,token]);

  async function act(action) {
    setError('');
    setBusy(true);
    try {
      let url = base+'/proposals'+version;
      let key = token, body;
      if (action !== 'plan') {
        key = secret.current.value.trim();
        if (!key) throw Error('Enter the local administrator key to record this action.');
        secret.current.value = '';
        url = '/v1/proposals/'+review.proposal.proposal_id+(action==='verify'?'/verify':'/decision');
        body = action==='verify' ? {request_id:crypto.randomUUID(), bundle_digest:review.proposal.bundle_digest}
          : {choice:action, verification_id:review.replay?.verification_id || null};
      }
      const res = await fetch(url,{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},
                                  body:body && JSON.stringify(body)});
      if (!res.ok) throw Error((await res.json()).detail || 'Request failed.');
      const data = await res.json();
      if (action==='plan') setReview(data);
      else if (action==='verify') setReview({...review, replay:data, verification_status:data.result.verdict});
      else setReview({...review, decision:data});
    } catch(e) {setError(e.message);}
    finally {setBusy(false);}
  }
  const p = review?.proposal;
  return <section aria-label="Repair review"><h2>Repair review</h2>
    {!p ? <button disabled={busy} onClick={()=>act('plan')}>Prepare proposal</button> : <>
      <p>{p.summary}</p>{p.change && <code>{p.change.setting} · {String(p.change.before)} → {String(p.change.after)}</code>}
      <p><strong>Risk</strong> {p.risk}</p>
      <details><summary>Proposal evidence and fixture</summary>
        <dl><dt>Bundle digest</dt><dd>{p.bundle_digest || 'No bound replay fixture'}</dd>
          <dt>Fixture ID</dt><dd>{p.fixture_id || 'Unavailable'}</dd>
          <dt>Source events</dt>{p.evidence.map(e=><dd key={e.event_id}>{e.event_id}</dd>)}</dl>
      </details>
      <p><strong>Fixture verification</strong> · {review.verification_status}</p>
      {review.replay && <><p>{review.replay.result.reason}</p>
        {review.replay.result.cases.length>0 && <div style={{overflowX:'auto'}}><table><thead><tr>
          {['Case','Expected','Baseline','Candidate'].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{review.replay.result.cases.map(c=><tr key={c.name}><td>{c.name.replaceAll('_',' ')}</td>
            <td>{c.expected}</td><td>{c.baseline}</td><td>{c.candidate}</td></tr>)}</tbody></table></div>}</>}
      {review.decision ? <p>Decision recorded · {review.decision.choice} · {review.decision.actor}</p> : <>
        <label>Local administrator key <input type="password" autoComplete="off" ref={secret}/></label>
        <p>Acceptance records a review decision. Application changes require a separate manual edit.</p>
        <button disabled={busy || !p.bundle_digest || review.verification_status!=='unverified'}
                onClick={()=>act('verify')}>Authorize fixture replay</button>{' '}
        <button disabled={busy || review.verification_status!=='passed'} onClick={()=>act('accept')}>Accept proposal</button>{' '}
        <button disabled={busy} onClick={()=>act('reject')}>Reject proposal</button>
      </>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
