import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTheme } from './contexts/ThemeContext';
import ExecutionPanel, { JsonView, Status } from './components/ExecutionPanel';
import { request, terminal } from './api';
import './App.css';

const modes = { planner: '多 Agent 编排', casual: '直接问答', web: '联网调研', file: '文件处理', coder: '代码验证', auto: '原自动路由' };
const examples = [
  { number: '01', title: '技术方案调研', desc: '搜索资料 → 对比方案 → 给出建议', mode: 'planner', query: '帮我调研 FastAPI 和 Flask 在内部研发工具中的适用场景。搜索官方资料，给出带来源的对比和选型建议。' },
  { number: '02', title: '最小方案验证', desc: '编写代码 → 实际执行 → 检查结论', mode: 'planner', query: '用 Python 标准库写一个最小示例，验证 asyncio.gather 可以并发运行三个短等待任务。实际运行，给出耗时与结论。请不要安装依赖。' },
  { number: '03', title: '测试驱动修复', desc: '加载 Skill → 修复代码 → 运行测试', mode: 'planner', skill: 'test-driven-code-repair', query: '请修复工作目录中的 [项目路径]。问题是：[描述错误]。测试命令是：[填写命令]。先复现，再做最小修改，运行测试并报告结果。' },
];
const dateText = (value) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const markdownComponents = {
  a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>,
  // Do not silently fetch tracking images contained in model/tool output.
  img: ({ src, alt }) => <a href={src} target="_blank" rel="noopener noreferrer">[图片：{alt || '点击查看'}]</a>,
};

export default function App() {
  const { isDark, toggleTheme } = useTheme();
  const [health, setHealth] = useState(null);
  const [connected, setConnected] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [skills, setSkills] = useState([]);
  const [checkpoints, setCheckpoints] = useState([]);
  const [capabilities, setCapabilities] = useState(null);
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('agenticseek-task'));
  const [task, setTask] = useState(null);
  const [view, setView] = useState('workspace');
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('planner');
  const [skill, setSkill] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [checkpointDetail, setCheckpointDetail] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const initialized = useRef(false);
  const inputRef = useRef(null);

  const selectTask = useCallback((id) => {
    setSelectedId(id); setTask(null); setView('workspace'); setError('');
    if (id) localStorage.setItem('agenticseek-task', id);
    else localStorage.removeItem('agenticseek-task');
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer;
    let iteration = 0;
    const controller = new AbortController();
    const options = { signal: controller.signal };
    async function poll() {
      try {
        const [runtime, history] = await Promise.all([request('/health', options), request('/tasks', options)]);
        if (cancelled) return;
        setHealth(runtime); setTasks(history); setConnected(true);
        if (!initialized.current) {
          initialized.current = true;
          if (!selectedId && runtime.active_task_id) selectTask(runtime.active_task_id);
        }
        if (selectedId) {
          try {
            const detail = await request('/tasks/' + selectedId, options);
            if (!cancelled) setTask(detail);
          } catch (err) {
            if (!cancelled) setError(err.message);
          }
        }
        if (iteration % 5 === 0) {
          const [availableSkills, saved] = await Promise.all([request('/skills', options), request('/checkpoints', options)]);
          if (!cancelled) { setSkills(availableSkills); setCheckpoints(saved); }
        }
        if (iteration % 15 === 0) {
          const caps = await request('/capabilities', options);
          if (!cancelled) setCapabilities(caps);
        }
        iteration++;
      } catch (err) {
        if (!cancelled && err.name !== 'AbortError') setConnected(false);
      } finally {
        if (!cancelled) timer = setTimeout(poll, 2000);
      }
    }
    poll();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [selectedId, selectTask, refreshKey]);

  const busy = Boolean(health?.active_task_id);
  const currentRunning = task && !terminal(task.status);
  const launch = async (path, body) => {
    if (sending) return;
    setSending(true); setError('');
    try {
      const created = await request(path, { method: 'POST', body: JSON.stringify(body || {}) });
      selectTask(created.id); setTask(created);
      setHealth(prev => ({ ...prev, active_task_id: created.id }));
      setQuery(''); setRefreshKey(k => k + 1);
    } catch (err) { setError(err.message); }
    finally { setSending(false); }
  };
  const submit = (event) => {
    event.preventDefault();
    if (!query.trim() || busy || !connected) return;
    if (query.includes('[项目路径]') || query.includes('[填写命令]')) {
      setError('请先把示例中的项目路径、错误描述和测试命令填写完整。');
      return;
    }
    launch('/tasks', { query: query.trim(), mode, skill: skill || null });
  };
  const stop = async () => {
    if (!task) return;
    try { setTask(await request('/tasks/' + task.id + '/stop', { method: 'POST' })); }
    catch (err) { setError(err.message); }
  };
  const copyAnswer = async () => {
    try { await navigator.clipboard.writeText(task.answer); setCopied(true); setTimeout(() => setCopied(false), 1800); }
    catch { setError('剪贴板不可用，请选中结果手动复制。'); }
  };
  const selectExample = (example) => {
    selectTask(null); setMode(example.mode); setSkill(example.skill || ''); setQuery(example.query);
    setTimeout(() => inputRef.current?.focus(), 0);
  };
  const inspectCheckpoint = async (id) => {
    try { setCheckpointDetail(await request('/checkpoints/' + id)); }
    catch (err) { setError(err.message); }
  };

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#workspace" onClick={(e) => { e.preventDefault(); setView('workspace'); }}>
        <span className="brand-icon">a<span>↗</span></span><span>agentic<span className="brand-light">seek</span><small>研发协作工作台</small></span>
      </a>
      <button className="new-task" onClick={() => { selectTask(null); setQuery(''); }}><span>＋</span> 新建任务</button>
      <nav aria-label="主导航">
        <button className={view === 'workspace' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('workspace')}><span>▦</span> 任务工作台</button>
        <button className={view === 'checkpoints' ? 'nav-item selected' : 'nav-item'} onClick={() => { setView('checkpoints'); setRefreshKey(k => k + 1); }}><span>↻</span> 断点恢复 <small>{checkpoints.filter(c => c.resumable).length}</small></button>
        <button className={view === 'skills' ? 'nav-item selected' : 'nav-item'} onClick={() => setView('skills')}><span>◇</span> Skill 库 <small>{skills.length}</small></button>
      </nav>
      <div className="history-label">最近任务 <span>{tasks.length}</span></div>
      <div className="history-list">
        {tasks.length === 0 && <p className="muted small">新任务会自动保存在这里。</p>}
        {tasks.map(row => <button key={row.id} className={'history-item ' + (selectedId === row.id ? 'active' : '')} onClick={() => selectTask(row.id)}>
          <span className={'history-dot dot-' + row.status} /><span className="history-text">{row.query}<small>{dateText(row.created_at)}</small></span>
        </button>)}
      </div>
      <div className="sidebar-footer"><span className={'connection-dot ' + (connected ? 'online' : '')} /><span>{connected ? '本地服务已连接' : '正在连接本地服务'}<small>{health?.model || '等待后端响应'}</small></span><button onClick={toggleTheme} aria-label={isDark ? '切换浅色主题' : '切换深色主题'}>{isDark ? '☀' : '☾'}</button></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div><span className="muted">工作空间</span><span className="breadcrumb">/</span><b>{view === 'workspace' ? '技术调研与方案验证' : view === 'skills' ? '可扩展能力' : view === 'history' ? '任务历史' : '运行恢复'}</b></div><div className="topbar-actions"><button className="text-button" onClick={() => setView('history')}>任务记录</button><span className="local-badge"><i /> LOCAL WORKSPACE</span></div></header>
      {!connected && <div className="banner warning" role="status">后端尚未连接。请运行项目根目录的 start_web.ps1，页面会自动重连。</div>}
      {capabilities && !capabilities.search_available && <div className="banner warning">{capabilities.search_hint}<button onClick={() => setRefreshKey(k => k + 1)}>重新检查</button></div>}
      {error && <div className="banner danger" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
      {busy && (!task || task.id !== health.active_task_id) && <div className="banner info">另一项任务正在运行。<button onClick={() => selectTask(health.active_task_id)}>查看执行进度 →</button></div>}

      {view === 'workspace' && <div className={'workspace-layout ' + (task ? 'with-task' : '')}>
        <main className="workspace-main">
          {!selectedId ? <section className="welcome">
            <div className="eyebrow"><span className="tiny-line" /> RESEARCH · BUILD · VERIFY</div>
            <h1>从一个问题，<br />走到<span>可验证的答案。</span></h1>
            <p className="hero-description">面向研发团队的技术调研与方案验证助手。<br />让多智能体协作，也让每一步执行有迹可循。</p>
            <div className="capability-strip"><span>↗ 联网调研</span><span>⌘ 代码与文件</span><span>✓ 反思与验证</span><span>↻ 故障恢复</span></div>
            <div className="examples">{examples.map(ex => <button className="example-card" key={ex.number} onClick={() => selectExample(ex)}><span className="example-number">{ex.number}<span>↗</span></span><h2>{ex.title}</h2><p>{ex.desc}</p></button>)}</div>
          </section> : task ? <section className="task-content">
            <div className="task-title"><div><span className="eyebrow">TASK / {task.id.slice(0, 8)}</span><h1>任务详情</h1></div><Status state={task.status} /></div>
            <article className="user-query"><span className="section-label">任务目标</span><p>{task.query}</p><div className="task-meta"><span>{modes[task.mode]}</span><span>{dateText(task.created_at)}</span>{task.skill && <span>◇ {task.skill}</span>}</div></article>
            {currentRunning && <div className="running-card" role="status"><span className="spinner" /><div><b>{task.stop_requested ? '正在安全停止' : task.phase || '任务执行中'}</b><p>{task.stop_requested ? '等待当前模型或工具调用结束；已成功的节点会保留。' : '可以切换页面或刷新，任务仍会在后台继续。'}</p></div><button className="secondary-button" onClick={stop} disabled={task.stop_requested}>停止任务</button></div>}
            {task.error && <div className="task-error"><b>{task.status === 'interrupted' ? '任务已中断' : '任务未完成'}</b><p>{task.error}</p></div>}
            {task.answer && <article className="answer-card"><div className="answer-heading"><span>✦ {task.status === 'succeeded' ? '执行结果' : '当前结果（不代表任务成功）'}</span><button className="text-button" onClick={copyAnswer}>{copied ? '已复制' : '复制结果'}</button></div><div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{task.answer}</ReactMarkdown></div></article>}
            {!task.answer && !currentRunning && <div className="empty-small">本次任务没有最终答案，请查看异常记录和断点信息。</div>}
            {task.checkpoint_id && <button className="text-button checkpoint-link" onClick={() => { setView('checkpoints'); setRefreshKey(k => k + 1); }}>↻ 查看此任务的断点与恢复条件</button>}
          </section> : <div className="loading-task" role="status"><span className="spinner" /> 正在读取任务…</div>}

          <form className="composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="task-input">任务内容</label>
            <textarea id="task-input" ref={inputRef} value={query} maxLength={32000} onChange={e => setQuery(e.target.value)} placeholder="描述你要调研的问题或验证的方案，附上文件路径、约束和验收标准…" onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submit(e); }} />
            <div className="composer-controls">
              <div className="composer-selects"><label><span>执行方式</span><select aria-label="执行方式" value={skill ? 'planner' : mode} disabled={Boolean(skill)} onChange={e => setMode(e.target.value)}>{Object.entries(modes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label><span>Skill</span><select aria-label="选择 Skill" value={skill} onChange={e => setSkill(e.target.value)}><option value="">按任务自由执行</option>{skills.map(s => <option key={s.name} value={s.name}>{s.name === 'test-driven-code-repair' ? '测试驱动代码修复' : s.name}</option>)}</select></label>
              </div>
              <button className="primary-button" type="submit" disabled={!query.trim() || sending || busy || !connected}>{sending ? '提交中…' : '开始任务'} <span>↗</span></button>
            </div>
            <div className="composer-note"><span>{skill ? `Skill 测试目录：${health?.workspace || '后端 WORK_DIR'}（修改 .env 后重启可切换）` : mode === 'auto' ? '原自动路由首次使用可能加载或下载分类模型。' : '每次提交是独立任务；支持工作目录内的本地文件路径。'}</span><span>Ctrl + Enter</span></div>
          </form>
          <footer className="workspace-footer"><span>仅限可信本地使用 · 工具可读写文件、执行代码</span><span>模型回复不等于测试通过</span></footer>
        </main>
        {task && <ExecutionPanel key={task.id} task={task} />}
      </div>}

      {view === 'history' && <main className="library-page"><span className="eyebrow">TASK HISTORY</span><h1>任务记录</h1><p className="page-description">刷新浏览器或重启服务后，已保存的结果仍可查看。这里显示最近 100 项任务。</p><div className="checkpoint-list">{tasks.map(row => <article className="checkpoint-card" key={row.id}><div className="checkpoint-card-main"><Status state={row.status} /><h2>{row.query}</h2><p>{modes[row.mode]} · {dateText(row.created_at)}</p></div><button className="secondary-button" onClick={() => selectTask(row.id)}>查看任务 →</button></article>)}</div>{!tasks.length && <div className="empty-small">暂无任务记录。</div>}</main>}

      {view === 'skills' && <main className="library-page"><span className="eyebrow">CAPABILITY LIBRARY</span><h1>Skill 库</h1><p className="page-description">不只是提示词：按需加载、约束计划、执行验证，并随断点保存版本。</p><div className="skill-grid">{skills.map(s => <article className="skill-card" key={s.name}><span className="skill-symbol">◇</span><span className="version">v{s.version}</span><h2>{s.name}</h2><p>{s.description}</p><div className="skill-tags">{s.required_agents.map(a => <span key={a}>{a}</span>)}</div><p className="small muted">{s.validator ? '✓ 已注册执行结果校验器' : '未配置独立校验器'}</p><button className="primary-button" onClick={() => { selectTask(null); setSkill(s.name); setMode('planner'); setQuery(''); }}>在任务中使用 ↗</button></article>)}</div>{!skills.length && <div className="empty-small">暂无已注册 Skill。</div>}</main>}

      {view === 'checkpoints' && <main className="library-page"><span className="eyebrow">CHECKPOINTS & RECOVERY</span><h1>从中断处，继续。</h1><p className="page-description">恢复中断或手动停止的工作流，复用已成功节点。最终失败的任务请先排查原因，再新建任务；不盲目重跑。</p>
        <div className="checkpoint-list">{checkpoints.map(c => <article className="checkpoint-card" key={c.task_id}><div className="checkpoint-card-main"><Status state={c.state} /><h2>{c.goal}</h2><p>{c.completed} / {c.total} 个节点完成 · {dateText(c.updated_at)}</p><code>{c.task_id}</code></div><div className="checkpoint-actions"><button className="secondary-button" onClick={() => inspectCheckpoint(c.task_id)}>查看记录</button><button className="primary-button" disabled={!c.resumable || busy || sending || !connected} onClick={() => launch('/checkpoints/' + c.task_id + '/resume')}>{c.resumable ? '继续执行 →' : '不可续跑'}</button></div></article>)}</div>
        {!checkpoints.length && <div className="empty-small">暂时没有断点。通过 Planner 执行任务后会自动保存。</div>}
        {checkpointDetail && <section className="checkpoint-detail"><div className="answer-heading"><b>断点执行记录</b><button className="text-button" onClick={() => setCheckpointDetail(null)}>收起</button></div><JsonView data={checkpointDetail} /></section>}
      </main>}
    </div>
  </div>;
}
