# runner controls
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import time

import runner


def rejected(action, exception=RuntimeError):
    try:
        action()
    except exception:
        return
    raise AssertionError('Negative control accepted')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build, output = args.build.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = {'test': runner.digest(__file__), 'runner': runner.digest(runner.__file__)}
    deadline = time.monotonic() + 180
    total = runner.cardinality(8)
    jobs = [{'n': 8, 'tree': index, 'start': start, 'count': min(317, total - start), 'seed': 20260915}
            for index, start in enumerate(range(0, total, 317))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: runner.run_block(build, output / str(job['tree']), job,
                                                             context, deadline, True), jobs))
    assert sum(record['job']['count'] for record in results) == total
    for record in results:
        job = record['job']
        certificate = output / str(job['tree']) / record['certificate']
        subprocess.run([sys.executable, str(build / 'reference_checker.py'), '--n', '8', '--trees', str(build / 'input.txt'),
                        '--cert', str(certificate), '--res', str(job['start']), '--mod', '1',
                        '--limit', str(job['count'])], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    first = output / '0'
    before = (first / 'complete.json').stat().st_mtime_ns
    assert runner.run_block(build, first, jobs[0], context, deadline) == results[0]
    assert (first / 'complete.json').stat().st_mtime_ns == before
    rejected(lambda: runner.run_block(build, first, jobs[0], {'changed': True}, deadline))
    record = results[0]
    certificate = first / record['certificate']
    original = certificate.read_bytes()
    certificate.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    rejected(lambda: runner.run_block(build, first, jobs[0], context, deadline))
    certificate.write_bytes(original)
    original_run = runner.command_run
    def corrupt(command, work, label, limit):
        elapsed = original_run(command, work, label, limit)
        if label == 'worker':
            path = work / 'cert.bin'
            raw = bytearray(path.read_bytes())
            raw[0] = raw[1]
            path.write_bytes(raw)
        return elapsed
    runner.command_run = corrupt
    try:
        rejected(lambda: runner.run_block(build, output / 'corrupt', jobs[0], context, deadline))
        assert not (output / 'corrupt/complete.json').exists()
    finally:
        runner.command_run = original_run
    bad = {**jobs[0], 'start': total - 1, 'count': 2}
    rejected(lambda: runner.run_block(build, output / 'overrun', bad, context, deadline))
    rejected(lambda: runner.run_block(build, output / 'expired', jobs[0], context, time.monotonic() - 1), runner.Interrupted)
    timeout = output / 'timeout'
    timeout.mkdir()
    started = time.monotonic()
    rejected(lambda: runner.command_run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout,
                                       'sleep', started + 0.2), runner.Interrupted)
    assert time.monotonic() - started < 2
    n12_jobs = [
        {'n': 12, 'tree': 0, 'start': 0, 'count': 17, 'seed': 20260915},
        {'n': 12, 'tree': 1, 'start': runner.cardinality(11) - 8, 'count': 17, 'seed': 20260915},
        {'n': 12, 'tree': 2, 'start': runner.cardinality(12) - 17, 'count': 17, 'seed': 20260915}]
    for job in n12_jobs:
        folder = output / ('boundary-' + str(job['tree']))
        record = runner.run_block(build, folder, job, context, deadline, True)
        subprocess.run([sys.executable, str(build / 'reference_checker.py'), '--n', '12', '--trees', str(build / 'input.txt'),
                        '--cert', str(folder / record['certificate']), '--res', str(job['start']), '--mod', '1',
                        '--limit', str(job['count'])], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    result = {'status': 'passed', 'full_n8_sequences': total, 'n8_blocks': len(jobs),
              'n12_boundary_sequences': 51,
              'controls': ['resume without recompute', 'changed context rejected', 'changed certificate rejected',
                           'corrupted solver output rejected before completion', 'range overrun rejected',
                           'expired block not started', 'running child killed at deadline',
                           'both C checkers and Python on all test certificates']}
    runner.atomic(output / 'TEST_PASSED.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
