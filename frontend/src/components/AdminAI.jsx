import React, {useEffect, useState, useRef} from 'react';
import { io } from 'socket.io-client'
import http from '../services/http';
import { SOCKET_BASE_URL } from '../services/apiBase'

export default function AdminAI(){
  const [logs, setLogs] = useState([]);
  const [metrics, setMetrics] = useState({ summary: {}, tools: [] });
  const [page, setPage] = useState(1);
  const [perPage] = useState(25);
  const [loading, setLoading] = useState(false);
  const [userId, setUserId] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [replayRunning, setReplayRunning] = useState(false);
  const [timeline, setTimeline] = useState([]);
  const [replayRunId, setReplayRunId] = useState(null);
  const socketRef = useRef(null);

  useEffect(()=>{ fetchLogs(); fetchMetrics(); }, [page]);

  useEffect(()=>{
    // setup socket
    socketRef.current = io(SOCKET_BASE_URL, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      withCredentials: true,
    })
    const s = socketRef.current
    s.on('connect', ()=>{})
    s.on('ai:replay:progress', (payload)=>{
      if(!payload || !payload.replay_run_id) return
      if(replayRunId && payload.replay_run_id !== replayRunId) return
      const entry = payload.entry
      setTimeline(t=>[...t, entry])
    })
    s.on('ai:replay:complete', (payload)=>{
      if(!payload || !payload.replay_run_id) return
      if(replayRunId && payload.replay_run_id !== replayRunId) return
      setReplayRunning(false)
    })
    return ()=>{ try{ s.disconnect() }catch(e){} }
  }, [replayRunId])

  function fetchLogs(){
    setLoading(true);
    const params = { page, per_page: perPage };
    if(userId) params.user_id = userId;
    http.get('/api/admin/ai/action-logs', { params })
      .then(r=>{
        if(r.data && r.data.items){
          setLogs(r.data.items || []);
        } else if(r.data && r.data.logs){
          setLogs(r.data.logs || []);
        } else {
          setLogs([]);
        }
      })
      .catch(()=>setLogs([]))
      .finally(()=>setLoading(false));
  }

  function fetchMetrics(){
    const params = {}
    if(userId) params.user_id = userId
    http.get('/api/admin/ai/metrics', { params })
      .then(r=>setMetrics(r.data || { summary: {}, tools: [] }))
      .catch(()=>setMetrics({ summary: {}, tools: [] }))
  }

  function startReplay(){
    if(!workflowId) return alert('Enter workflow id to replay')
    setTimeline([])
    setReplayRunning(true)
    http.post(`/api/admin/ai/replay/${workflowId}`, {})
      .then(r=>{
        if(r && r.replay_run_id){
          setReplayRunId(r.replay_run_id)
        }
        if(r && r.timeline){
          setTimeline(r.timeline || [])
        }
      })
      .catch(err=>{
        alert('Replay failed: ' + (err.message || ''))
        setReplayRunning(false)
      })
  }

  return (
    <div className="admin-ai">
      <h3>AI Action Logs</h3>
      <div style={{marginBottom:12}}>
        <input placeholder="Filter by user_id" value={userId} onChange={e=>setUserId(e.target.value)} />
        <button onClick={()=>{ setPage(1); fetchLogs(); fetchMetrics(); }}>Filter</button>
      </div>
      <div style={{marginBottom:16, padding:12, border:'1px solid #eee', borderRadius:8}}>
        <strong>Tool Metrics</strong>
        <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(180px, 1fr))', gap:12, marginTop:8}}>
          <div><div>Total actions</div><strong>{metrics.summary?.total_actions ?? 0}</strong></div>
          <div><div>Avg duration</div><strong>{Math.round(metrics.summary?.avg_duration_ms || 0)} ms</strong></div>
          <div><div>Precise avg</div><strong>{Math.round(metrics.summary?.precise_avg_ms || 0)} ms</strong></div>
          <div><div>AI latency</div><strong>{Math.round(metrics.summary?.avg_ai_response_latency_ms || 0)} ms</strong></div>
          <div><div>Websocket latency</div><strong>{Math.round(metrics.summary?.avg_websocket_latency_ms || 0)} ms</strong></div>
          <div><div>Failures</div><strong>{metrics.summary?.failure_count ?? 0}</strong></div>
          <div><div>Retries</div><strong>{metrics.summary?.retry_count ?? 0}</strong></div>
          <div><div>Success rate</div><strong>{Math.round(metrics.summary?.workflow_success_rate || 0)}%</strong></div>
          <div><div>Slowest</div><strong>{Math.round(metrics.summary?.slowest_ms || 0)} ms</strong></div>
        </div>
      </div>
      {loading? <div>Loading...</div> : (
        <table style={{width:'100%', borderCollapse:'collapse'}}>
          <thead>
            <tr>
              <th>ID</th><th>User</th><th>Action</th><th>Tool</th><th>Intent</th><th>Status</th><th>Created</th>
            </tr>
          </thead>
          <tbody>
            {logs.map(l=> (
              <tr key={l.id} style={{borderTop:'1px solid #eee'}}>
                <td>{l.id}</td>
                <td>{l.user_id}</td>
                <td>{l.action}</td>
                <td>{l.tool_name}</td>
                <td>{l.intent}</td>
                <td>{l.status}</td>
                <td>{l.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{marginTop:16}}>
        <h4>Replay Workflow</h4>
        <div style={{display:'flex', gap:8, alignItems:'center'}}>
          <input placeholder="Workflow id" value={workflowId} onChange={e=>setWorkflowId(e.target.value)} />
          <button onClick={startReplay} disabled={replayRunning}>{replayRunning? 'Replaying...' : 'Start Replay'}</button>
        </div>

        <div style={{marginTop:12}}>
          <h5>Execution Timeline</h5>
          <div style={{borderLeft:'2px solid #eee', paddingLeft:12}}>
            {timeline.length===0? <div style={{color:'#666'}}>No timeline yet</div> : timeline.map((t,i)=> (
              <div key={`${t.step}-${i}`} style={{marginBottom:12}}>
                <div style={{display:'flex', justifyContent:'space-between'}}>
                  <div><strong>Step {t.step}</strong> — {t.action} <span style={{color:t.status==='success'?'green':'red', marginLeft:8}}> {t.status}</span></div>
                  <div><small>{t.duration_ms} ms</small></div>
                </div>
                <div style={{fontSize:13, color:'#444', marginTop:4}}>
                  <div><strong>Tool:</strong> {t.tool} <strong style={{marginLeft:8}}>Intent:</strong> {t.intent}</div>
                  <details style={{marginTop:6}}>
                    <summary>Payloads</summary>
                    <pre style={{whiteSpace:'pre-wrap', maxHeight:240, overflow:'auto'}}>{JSON.stringify(t.request_payload, null, 2)}</pre>
                    <pre style={{whiteSpace:'pre-wrap', maxHeight:240, overflow:'auto'}}>{JSON.stringify(t.response_payload, null, 2)}</pre>
                    {t.error_message? <div style={{color:'red'}}><strong>Error:</strong> {t.error_message}</div> : null}
                  </details>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{marginTop:16}}>
        <h4>Top Tools</h4>
        <table style={{width:'100%', borderCollapse:'collapse'}}>
          <thead>
            <tr>
              <th>Tool</th><th>Executions</th><th>Avg Duration</th><th>Failures</th><th>Retries</th><th>Max Duration</th>
            </tr>
          </thead>
          <tbody>
            {(metrics.tools || []).map(row=> (
              <tr key={row.tool_name} style={{borderTop:'1px solid #eee'}}>
                <td>{row.tool_name}</td>
                <td>{row.executions}</td>
                <td>{Math.round(row.avg_duration_ms || 0)} ms</td>
                <td>{row.failure_count}</td>
                <td>{row.retried_count}</td>
                <td>{Math.round(row.max_duration_ms || 0)} ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{marginTop:12}}>
        <button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page<=1}>Prev</button>
        <span style={{margin:'0 8px'}}>Page {page}</span>
        <button onClick={()=>setPage(p=>p+1)}>Next</button>
      </div>
    </div>
  )
}
