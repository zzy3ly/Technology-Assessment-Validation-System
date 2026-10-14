import React, { useState } from 'react';
import { apiUrl, labels, terminal } from '../api';

export function Status({ state }) {
  return <span className={`status status-${state || 'queued'}`}><i />{labels[state] || state || '等待执行'}</span>;
}

export function JsonView({ data }) {
  return <pre className="json-view">{typeof data === 'string' ? data : JSON.stringify(data, null, 2)}</pre>;
}

export default function ExecutionPanel({ task }) {
  const [tab, setTab] = useState('plan');
  const [imageFailed, setImageFailed] = useState(false);
  const w = task.workflow || {};
  const plan = w.plan || [];
  const completed = new Set((w.completed_steps || []).map(String));
  const active = !terminal(task.status);
  const selectedSkill = w.selected_skill?.name || task.skill;
  const validation = w.skill_validation || {};
  const tokens = task.token_usage?.total_tokens;

  return <aside className="execution-panel" aria-label="执行详情">
    <div className="panel-heading"><div><span className="eyebrow">EXECUTION TRACE</span><h2>执行详情</h2></div><Status state={w.current_state || task.status} /></div>
    <div className="runtime-stats">
      <div><strong>{completed.size}<small> / {plan.length || '—'}</small></strong><span>完成节点</span></div>
      <div><strong>{w.replan_count || 0}</strong><span>重新规划</span></div>
      <div><strong>{tokens == null ? '—' : tokens.toLocaleString()}</strong><span>已用 tokens</span></div>
    </div>
    <div className="panel-tabs" role="tablist" aria-label="执行详情分类">
      {[['plan', '工作流'], ['tools', '工具结果'], ['verify', '校验与恢复']].map(([key, name]) =>
        <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{name}</button>)}
    </div>
    <div className="panel-content" role="tabpanel">
      {tab === 'plan' && <>
        <p className="muted small">{active ? task.phase : '以下为本次任务的实际执行记录。'}</p>
        {!plan.length && <div className="empty-small">{task.mode === 'planner' ? '计划生成后，节点和依赖会显示在这里。' : '当前是直接执行模式，不生成多节点工作流。'}</div>}
        <ol className="node-list">{plan.map(([name, node], index) => {
          const done = completed.has(String(node.id));
          const failed = Boolean(w.failures?.[node.id]) && !done;
          const current = !done && w.cursor === index && active;
          return <li key={`${node.id}-${index}`} className={done ? 'node-done' : failed ? 'node-failed' : current ? 'node-active' : ''}>
            <div className="node-marker">{done ? '✓' : index + 1}</div>
            <div className="node-body"><span className="agent-label">{node.agent}<span>{done ? '已完成' : failed ? '失败 / 恢复中' : current ? '执行中' : '待执行'}</span></span>
              <h3>{name.length > 80 ? `节点 ${index + 1} · ${node.agent}` : name}</h3><p>{node.task}</p>
              {!!node.need?.length && <small>依赖节点：{node.need.join('、')}</small>}
              {w.tool_outputs?.[node.id] && <details><summary>查看节点输出</summary><JsonView data={w.tool_outputs[node.id]} /></details>}
            </div>
          </li>;
        })}</ol>
        {!!w.state_history?.length && <details><summary>状态转换记录 · {w.state_history.length}</summary>
          <ol className="transition-list">{w.state_history.map((item, i) => <li key={i}><b>{labels[item.from] || item.from} → {labels[item.to] || item.to}</b><span>{item.event}</span>{item.detail && <p>{item.detail}</p>}</li>)}</ol>
        </details>}
      </>}
      {tab === 'tools' && <>
        <p className="muted small">展示真实工具返回，不用模型的文字承诺代替执行证据。</p>
        {!task.blocks?.length && <div className="empty-small">尚无工具调用结果。</div>}
        {(task.blocks || []).map((block, i) => <details key={i} className="evidence-card"><summary>{block.agent} · {block.tool_type || block.tool_name || block.tag || `调用 ${i + 1}`}</summary><JsonView data={block} /></details>)}
        {task.browser_used && active && !imageFailed && <div className="browser-preview"><h3>浏览器画面</h3><img src={apiUrl(`/tasks/${task.id}/screenshot?t=${Math.floor(Date.now() / 5000)}`)} alt="当前任务浏览器页面" onError={() => setImageFailed(true)} /></div>}
        <p className="muted small">浏览器画面仅在当前任务访问网页时提供，不展示历史任务的旧截图。</p>
      </>}
      {tab === 'verify' && <>
        <h3 className="section-label">Skill 验证</h3>
        {selectedSkill ? <div className="evidence-card"><b>{selectedSkill}</b><p className="muted small">{w.selected_skill?.version ? `版本 ${w.selected_skill.version} · 已按需加载` : '已选择，等待加载'}</p>
          {Object.keys(validation).length ? <JsonView data={validation} /> : <p className="muted small">尚无验证结果，不代表测试通过。</p>}
        </div> : <p className="muted small">此任务未选择 Skill。</p>}
        <h3 className="section-label">反思与评估</h3>
        {Object.keys(w.review_contexts || {}).length ? Object.entries(w.review_contexts).map(([key, value]) => <details className="evidence-card" key={key}><summary>评审节点 {key}</summary><JsonView data={value} /></details>) : <p className="muted small">尚无评审记录；是否评审由实际计划决定。</p>}
        <h3 className="section-label">异常与恢复</h3>
        {w.error_history?.length ? w.error_history.map((error, i) => <details className="evidence-card" key={i}><summary>{error.kind || error.source || `异常 ${i + 1}`}</summary><JsonView data={error} /></details>) : <p className="muted small">暂无已记录的异常。</p>}
        {task.checkpoint_id && <><h3 className="section-label">Checkpoint</h3><code className="checkpoint-id">{task.checkpoint_id}</code><p className="muted small">成功节点会被保留。到「断点恢复」查看可继续的任务。</p></>}
      </>}
    </div>
  </aside>;
}
