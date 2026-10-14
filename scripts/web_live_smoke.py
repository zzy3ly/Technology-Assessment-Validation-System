"""Paid, explicitly opt-in live tests of the actual HTTP -> Agent pipeline.

Use an isolated WORK_DIR when testing Skill code repair, never a real codebase.
This script records results, not benchmark success-rate claims.
"""
import argparse
import json
import time
from pathlib import Path

import httpx


def run_task(client, query, mode='planner', skill=None, timeout=360):
    response = client.post('/api/tasks', json={'query': query, 'mode': mode, 'skill': skill})
    response.raise_for_status()
    task_id = response.json()['id']
    started = time.monotonic()
    previous = None
    while time.monotonic() - started < timeout:
        row = client.get('/api/tasks/' + task_id).json()
        workflow = row.get('workflow') or {}
        progress = (row['status'], workflow.get('current_state'), len(workflow.get('completed_steps', [])))
        if progress != previous:
            print(json.dumps({'task': task_id, 'progress': progress, 'tokens': row.get('token_usage')}, ensure_ascii=False), flush=True)
            previous = progress
        if row['status'] in ('succeeded', 'failed', 'stopped'):
            return {'elapsed_seconds': round(time.monotonic() - started, 2), **row}
        time.sleep(2)
    client.post('/api/tasks/' + task_id + '/stop')
    raise TimeoutError(f'Task {task_id} exceeded smoke-test deadline; stop requested.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:7777')
    parser.add_argument('--case', choices=['workflow', 'skill'], required=True)
    parser.add_argument('--allow-paid-model', action='store_true')
    args = parser.parse_args()
    if not args.allow_paid_model:
        parser.error('Live tests require --allow-paid-model.')
    with httpx.Client(base_url=args.url, trust_env=False, timeout=15) as client:
        if args.case == 'workflow':
            query = ('Web 工作流联调。请用最短的两步计划完成：1. 让 coder 使用 Python 标准库实际执行 '
                     'print(sum(i*i for i in range(1,6)))，不要写文件或安装依赖；'
                     '2. 让 casual 根据工具输出，用中文报告计算结果。不要联网，也不需要额外扩展任务。')
            row = run_task(client, query)
        else:
            directory = Path(client.get('/api/health').json()['workspace']).resolve()
            # Only allow this dedicated fixture path, never modify the user's normal workspace.
            if directory.name != 'web-skill-smoke':
                raise ValueError('Skill smoke server must use a dedicated WORK_DIR named web-skill-smoke.')
            query = (f'请使用测试驱动代码修复 Skill 修复现有项目 {directory.as_posix()}。'
                     'calculator.py 的 add 函数错误地做了减法，test_calculator.py 是现有 unittest 测试。'
                     '只操作该目录；先读取两个文件并运行 python -m unittest discover -v 复现，'
                     '定位根因后只最小修改 calculator.py，然后重新运行全部测试。'
                     '不要修改或删除测试，不要安装依赖，不要联网。尽量合并 coder 步骤，最后用中文报告证据。')
            row = run_task(client, query, skill='test-driven-code-repair')
        report = Path(__file__).resolve().parents[1] / '.web-runtime' / f'live-{args.case}.json'
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': row['status'], 'task_id': row['id'], 'report': str(report),
                          'tokens': row['token_usage'], 'error': row['error']}, ensure_ascii=False), flush=True)
        if row['status'] != 'succeeded':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
