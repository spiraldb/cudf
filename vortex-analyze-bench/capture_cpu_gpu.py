#!/usr/bin/env python3
"""One bounded Q5 CPU/GPU capture; no build, patches, or optimization changes."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

from bench import require_idle_gpu

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'cpp/build/vortex-analysis-20261009'
BINARY = ROOT / 'cpp/build/ndsh-review-20260923-115320-on/benchmarks/NDSH_Q05_NVBENCH'
PREFIX = OUT / 'q5-cpu-gpu'

def main():
    if not OUT.is_dir() or list(OUT.glob('q5-cpu-gpu*')):
        raise RuntimeError('Expected prepared output directory with no existing q5-cpu-gpu outputs')
    dirty = subprocess.check_output(
        ['git', '--no-pager', '-C', '/home/ubuntu/vortex', '--no-optional-locks', 'status', '--porcelain'],
        text=True)
    if dirty:
        raise RuntimeError('Vortex source is not clean')
    head = subprocess.check_output(
        ['git', '--no-pager', '-C', '/home/ubuntu/vortex', 'rev-parse', 'HEAD'], text=True).strip()
    digest = hashlib.sha256(BINARY.read_bytes()).hexdigest()
    if digest != '5551f38355bf0cfd96091207310f274f778b5fd176086035daf95680c6c9dcd1':
        raise RuntimeError('Executable differs from the previously validated function-cache build')
    command = [
        'sudo', '-n', 'env', 'CUDA_VISIBLE_DEVICES=0', 'KVIKIO_COMPAT_MODE=ON',
        f'TMPDIR={OUT}', 'nsys', 'profile', '--trace=cuda,nvtx,osrt',
        '--sample=process-tree', '--backtrace=fp', '--sampling-period=2000000',
        '--cpuctxsw=process-tree',
        '--ftrace=sched/sched_switch,sched/sched_wakeup,sched/sched_waking',
        '--osrt-file-access=true', '--cuda-event-trace=true',
        '--capture-range=cudaProfilerApi', '--capture-range-end=stop',
        '--export=sqlite', f'--output={PREFIX}', str(BINARY),
        '--benchmark', 'ndsh_q5_local', '--profile', '--devices', '0',
        '--axis', 'scale_factor=10', '--axis', 'format=vortex', '--axis', 'workload=q5',
        '--axis', 'cache=warm', '--axis', 'io=buffered', '--json', str(PREFIX) + '.json',
    ]
    require_idle_gpu()
    print(f'Vortex {head}\nExecutable SHA256 {digest}\n{command}', flush=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('VORTEX_CUDA_')}
    with PREFIX.with_suffix('.log').open('x') as log:
        log.write(f'Vortex {head}\nExecutable SHA256 {digest}\n{command}\n')
        log.flush()
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=240)
        except BaseException:
            if process.poll() is None:
                subprocess.run(['sudo', '-n', 'kill', '-TERM', '--', f'-{process.pid}'],
                               timeout=10, check=False)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    subprocess.run(['sudo', '-n', 'kill', '-KILL', '--', f'-{process.pid}'],
                                   timeout=10, check=False)
                    process.wait(timeout=5)
            raise
    if code:
        raise RuntimeError(f'Capture exited {code}; see {PREFIX}.log')
    paths = [str(PREFIX) + ext for ext in ('.sqlite', '.nsys-rep', '.json')]
    subprocess.run(['sudo', '-n', 'chown', f'{os.getuid()}:{os.getgid()}', *paths],
                   timeout=10, check=True)
    data = json.loads(PREFIX.with_suffix('.json').read_text())
    states = [s for b in data['benchmarks'] for s in b['states']]
    assert len(states) == 1
    metrics = {s['tag']: s['data'][0]['value'] for s in states[0]['summaries'] if s.get('data')}
    expected = {'nv/element_count/Rows': '76590427', 'file_size': '5287549556',
                'ndsh/q5/matched_rows': '51468', 'ndsh/q5/countries': '5'}
    assert {key: metrics[key] for key in expected} == expected
    print('Capture completed; input/result signatures match. Profile timer is NOT throughput evidence.')
    print(f'Report: {PREFIX}.nsys-rep\nSQLite: {PREFIX}.sqlite\nLog: {PREFIX}.log')


if __name__ == '__main__':
    main()
