import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { ThemeProvider } from './contexts/ThemeContext';
import ExecutionPanel from './components/ExecutionPanel';

jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }) => <div>{children}</div> }));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));

const skill = { name: 'test-driven-code-repair', version: '0.1.0', description: '测试驱动修复', required_agents: ['file', 'coder'], validator: true };
let records;
let current;
let activeId;

beforeEach(() => {
  localStorage.clear(); records = []; current = null; activeId = null;
  global.fetch = jest.fn(async (url, options = {}) => {
    let data;
    if (url.endsWith('/health')) data = { model: 'deepseek-chat', active_task_id: activeId };
    else if (url.endsWith('/skills')) data = [skill];
    else if (url.endsWith('/checkpoints')) data = [];
    else if (url.endsWith('/capabilities')) data = { search_available: true };
    else if (url.endsWith('/stop')) { current = { ...current, status: 'stopping', stop_requested: true }; data = current; }
    else if (url.endsWith('/tasks') && options.method === 'POST') {
      current = { ...JSON.parse(options.body), id: 'task-1', status: 'running', answer: '', blocks: [], created_at: '2026-01-01', token_usage: {} };
      records = [current]; activeId = current.id; data = current;
    } else if (url.endsWith('/tasks')) data = records;
    else data = current;
    return { ok: true, json: async () => data };
  });
});

function open() { return render(<ThemeProvider><App /></ThemeProvider>); }

test('renders workspace and real registered skills', async () => {
  open();
  expect(screen.getByText('可验证的答案。')).toBeInTheDocument();
  await screen.findByText('本地服务已连接');
  fireEvent.click(screen.getByRole('button', { name: /Skill 库/ }));
  await screen.findByRole('heading', { name: 'test-driven-code-repair' });
  expect(screen.getByText('✓ 已注册执行结果校验器')).toBeInTheDocument();
});

test('submits selected skill through real API contract', async () => {
  open();
  await screen.findByText('本地服务已连接');
  await screen.findByRole('option', { name: '测试驱动代码修复' });
  fireEvent.change(screen.getByLabelText('选择 Skill'), { target: { value: skill.name } });
  fireEvent.change(screen.getByLabelText('任务内容'), { target: { value: '修复我的示例代码并测试' } });
  fireEvent.click(screen.getByRole('button', { name: /开始任务/ }));
  await screen.findByRole('heading', { name: '任务详情' });
  const call = global.fetch.mock.calls.find(([url, options]) => url.endsWith('/tasks') && options.method === 'POST');
  expect(JSON.parse(call[1].body)).toEqual({ query: '修复我的示例代码并测试', mode: 'planner', skill: skill.name });
  expect(localStorage.getItem('agenticseek-task')).toBe('task-1');
});

test('failed persisted task does not show successful result', async () => {
  localStorage.setItem('agenticseek-task', 'failed-id');
  current = { id: 'failed-id', status: 'failed', query: 'test', mode: 'planner', answer: '验证未通过', error: '测试命令失败', blocks: [] };
  records = [current];
  open();
  await screen.findByText('测试命令失败');
  expect(screen.getByText(/当前结果（不代表任务成功）/)).toBeInTheDocument();
  expect(screen.queryByText('已完成')).not.toBeInTheDocument();
});

test('stop waits for actual backend termination', async () => {
  localStorage.setItem('agenticseek-task', 'task-1'); activeId = 'task-1';
  current = { id: 'task-1', status: 'running', query: 'test', mode: 'casual', answer: '', blocks: [] };
  records = [current];
  open();
  fireEvent.click(await screen.findByRole('button', { name: '停止任务' }));
  await screen.findByText('正在安全停止');
  expect(screen.getByRole('button', { name: '停止任务' })).toBeDisabled();
  expect(screen.queryByText('已完成')).not.toBeInTheDocument();
});

test('workflow progress and skill evidence are visible', () => {
  render(<ExecutionPanel task={{ id: 'a', mode: 'planner', status: 'running', token_usage: { total_tokens: 321 }, workflow: {
    current_state: 'running', cursor: 1, completed_steps: ['1'], plan: [['读取文件', { id: '1', agent: 'file', task: 'inspect' }], ['运行测试', { id: '2', agent: 'coder', task: 'execute', need: ['1'] }]],
    selected_skill: { name: skill.name, version: '0.1.0' }, skill_validation: { passed: false, errors: ['Full test suite failed'] },
  } }} />);
  expect(screen.getByText('读取文件')).toBeInTheDocument();
  expect(screen.getByText('依赖节点：1')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '校验与恢复' }));
  expect(screen.getByText(/Full test suite failed/)).toBeInTheDocument();
});

test('unavailable server disables submission and shows reconnect notice', async () => {
  global.fetch.mockRejectedValue(new Error('offline'));
  open();
  fireEvent.change(screen.getByLabelText('任务内容'), { target: { value: 'hello' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /开始任务/ })).toBeDisabled());
  expect(screen.getByText(/后端尚未连接/)).toBeInTheDocument();
});
