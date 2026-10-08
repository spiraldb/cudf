#!/usr/bin/env python3
"""Run one ordinary SF10 Vortex Q5 benchmark; optionally compare a previous JSON."""
import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_results(path):
    results = {}
    for benchmark in json.loads(Path(path).read_text())['benchmarks']:
        for state in benchmark['states']:
            axes = {a['name']: a['value'] for a in state['axis_values']}
            metrics = {s['tag']: s['data'][0]['value'] for s in state['summaries'] if s.get('data')}
            if 'nv/cold/time/cpu/mean' not in metrics:
                raise ValueError('Expected normal, unprofiled benchmark results')
            workload = axes['workload']
            if workload in results:
                raise ValueError('Expected one state per workload')
            results[workload] = {
                'ms': float(metrics['nv/cold/time/cpu/mean']) * 1000,
                'noise_pct': float(metrics['nv/cold/time/cpu/stdev/relative']) * 100,
                'samples': int(metrics['nv/cold/sample_size']),
                'axes': axes,
                'signature': {k: v for k, v in metrics.items()
                              if k.startswith('ndsh/') or k in ('file_size', 'nv/element_count/Rows')},
            }
    return results


def print_results(current, previous=None):
    print('Workload      Mean ms   Sample noise   Samples   Change vs before')
    for workload, result in current.items():
        change = '-'
        if previous is not None:
            before = previous[workload]
            if result['axes'] != before['axes'] or result['signature'] != before['signature']:
                raise ValueError('Inputs, settings or result signatures differ; refusing comparison')
            change = f"{100 * (result['ms'] / before['ms'] - 1):+.2f}%"
        print(f"{workload:<10} {result['ms']:>10.3f} {result['noise_pct']:>12.2f}%"
              f" {result['samples']:>9}   {change}")
    print('Change compares process means; sample noise is not between-process repeatability.')


def require_idle_gpu():
    apps = subprocess.check_output(['nvidia-smi', '-i', '0', '--query-compute-apps=pid',
                                    '--format=csv,noheader'], text=True).strip()
    utilization = subprocess.check_output(['nvidia-smi', '-i', '0', '--query-gpu=utilization.gpu',
                                           '--format=csv,noheader,nounits'], text=True).strip()
    if apps or utilization != '0':
        raise RuntimeError('GPU 0 is busy; no benchmark started')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path, help='Explicit Q5 executable; never rebuilt here')
    parser.add_argument('--label', required=True, help='Unique result name, e.g. before or after')
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--compare', type=Path, help='Previous normal benchmark JSON')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.label) or args.samples < 1:
        parser.error('Use a simple filename label and a positive sample count')
    binary = args.binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error(f'Not an executable: {binary}')
    previous = load_results(args.compare) if args.compare else None
    output = HERE / 'results' / (args.label + '.json')
    log = output.with_suffix('.log')
    if output.exists() or log.exists():
        parser.error('Results already exist; choose a new label')
    output.parent.mkdir(exist_ok=True)
    command = [str(binary), '--benchmark', 'ndsh_q5_local', '--devices', '0',
               '--axis', 'scale_factor=10', '--axis', 'format=vortex', '--axis', 'workload=[read,q5]',
               '--axis', 'cache=warm', '--axis', 'io=buffered', '--stopping-criterion', 'sample-count',
               '--target-samples', str(args.samples), '--timeout', '120', '--json', str(output)]
    env = {k: v for k, v in os.environ.items() if not k.startswith('VORTEX_CUDA_')}
    env.update(CUDA_VISIBLE_DEVICES='0', KVIKIO_COMPAT_MODE='ON', TMPDIR=str(ROOT / 'cpp/build'))
    require_idle_gpu()
    print(f'Running {binary}\nSF10, warm buffered, {args.samples} samples/state. Log: {log}', flush=True)
    with log.open('x') as stream:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=210)
        except BaseException:
            # Stop the benchmark's whole process group, including on Ctrl-C or timeout.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            raise
    if code:
        raise RuntimeError(f'Benchmark failed ({code}); see {log}')
    current = load_results(output)
    if set(current) != {'read', 'q5'} or any(r['samples'] != args.samples for r in current.values()):
        raise ValueError('Unexpected workload or sample count')
    print_results(current, previous)
    print(f'Results: {output}')


if __name__ == '__main__':
    main()
