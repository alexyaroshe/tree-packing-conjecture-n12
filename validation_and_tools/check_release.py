# portable package check
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {'worker.c', 'check_a.c', 'check_b.c', 'reference_solver.py', 'sat_solver.py',
            'reference_checker.py', 'audit_input.py', 'build.py',
            'gate_target.py', 'gate.sh', 'gen_input.sh', 'tests/test_checker.py',
            'tests/test_strict.py', 'tests/test_warm.py'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_package():
    listed = set()
    for line in (ROOT / 'SHA256SUMS').read_text().splitlines():
        expected, separator, relative = line.partition('  ')
        if (not separator or len(expected) != 64
                or any(char not in '0123456789abcdef' for char in expected)
                or relative in listed or relative == 'SHA256SUMS'):
            raise RuntimeError('Invalid package checksum entry')
        source = ROOT / relative
        if (ROOT not in source.parents or source.resolve() != source or not source.is_file()
                or digest(source) != expected):
            raise RuntimeError('Package checksum mismatch: ' + relative)
        listed.add(relative)
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob('*')
              if path.is_file() and path != ROOT / 'SHA256SUMS'
              and not {'.git', '__pycache__'}.intersection(path.relative_to(ROOT).parts)}
    if listed != actual:
        raise RuntimeError('Package checksum file set mismatch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    if sys.flags.optimize:
        raise RuntimeError('Python assertions must remain enabled')
    check_package()
    import networkx
    compiler = shutil.which(args.cc)
    if compiler is None:
        raise RuntimeError('C compiler missing: ' + args.cc)
    bundle = json.loads((ROOT / 'BUNDLE.json').read_text())
    mapping = bundle['runtime_sources']
    if (set(mapping) != REQUIRED
            or len(mapping) != len(bundle['files']) or set(mapping.values()) != set(bundle['files'])):
        raise RuntimeError('Runtime source mapping mismatch')
    for relative, expected in bundle['files'].items():
        source = ROOT / relative
        if (ROOT not in source.parents or source.resolve() != source or not source.is_file()
                or digest(source) != expected):
            raise RuntimeError('Frozen source mismatch: ' + relative)
    plan = json.loads((ROOT / 'runner_and_tests/plan.json').read_text())
    if digest(ROOT / 'trees.txt') != plan['input_sha256']:
        raise RuntimeError('Input catalog mismatch')
    if digest(ROOT / 'BUNDLE.json') != plan['bundle_sha256']:
        raise RuntimeError('Bundle identity mismatch')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    build = output / 'build'
    build.mkdir()
    for name, relative in mapping.items():
        target = build / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copy2(ROOT / 'BUNDLE.json', build / 'BUNDLE.json')
    shutil.copy2(ROOT / 'trees.txt', build / 'input.txt')
    environment = dict(os.environ)
    environment.pop('PYTHONOPTIMIZE', None)
    environment.update(PYTHONDONTWRITEBYTECODE='1', LC_ALL='C')
    started = time.monotonic()
    passed = []

    def run(label, command, extra=None, expected=None):
        print(label, flush=True)
        log = output / (label + '.log')
        with log.open('w') as stream:
            subprocess.run([str(item) for item in command], cwd=build,
                           env={**environment, **(extra or {})}, stdout=stream,
                           stderr=subprocess.STDOUT, check=True, timeout=600)
        if expected and expected not in log.read_text():
            raise RuntimeError('Expected result missing: ' + label)
        passed.append(label)

    for binary in ('worker', 'check_a', 'check_b'):
        run('build-' + binary, [compiler, '-std=c11', '-D_POSIX_C_SOURCE=200809L',
                               '-O3', '-Wall', '-Wextra',
                               build / (binary + '.c'), '-o', build / binary])
    run('input-audit', [sys.executable, build / 'audit_input.py', build / 'input.txt', 12],
        expected='AUDIT PASSED for k=2..12')
    certificate = output / 'n9-prefix.cert'
    run('n9-prefix', [build / 'worker', '--n', 9, '--trees', build / 'input.txt',
                      '--limit', 2, '--cert', certificate])
    example = ROOT / 'tests/examples'
    generated_example = output / 'example.cert'
    run('example-worker', [build / 'worker', '--n', 3, '--trees', example / 'trees.txt',
                           '--cert', generated_example])
    if generated_example.read_bytes() != (example / 'packing.cert').read_bytes():
        raise RuntimeError('Worked example certificate mismatch')
    for binary in ('check_a', 'check_b', 'reference_checker.py'):
        command = [sys.executable, build / binary] if binary.endswith('.py') else [build / binary]
        run('example-' + binary, [*command, '--n', 3, '--trees', example / 'trees.txt',
                                  '--cert', example / 'packing.cert'],
            expected='1 certificates')
    for binary in ('check_a', 'check_b'):
        work = output / ('controls-' + binary)
        extra = {'CHECKER_BINARY': str(build / binary), 'RUN_TEST_WORK': str(work),
                 'RUN_N9_CERT': str(certificate)}
        for script, expected in [('test_checker.py', 'PASS 1596 differential cases'),
                                 ('test_strict.py', 'PASS 30 strict parser tests'),
                                 ('test_warm.py', 'PASS 668 warmed-cache differential controls')]:
            run(binary + '-' + script[:-3], [sys.executable, build / 'tests' / script],
                extra, expected)
    for part, script in [('runner', 'test_runner.py'), ('campaign', 'test_campaign.py')]:
        target = output / (part + '-controls')
        run(part + '-controls', [sys.executable, ROOT / 'runner_and_tests' / script,
                                '--build', build, '--output', target])
        result = json.loads((target / 'TEST_PASSED.json').read_text())
        if result['status'] != 'passed' or result['full_n8_sequences'] != 9108:
            raise RuntimeError('Controller controls incomplete: ' + part)
    result = {'status': 'passed', 'scope': 'Portable package checks; not the Linux production gate or exhaustive n=12 rerun.',
              'system': platform.system(), 'machine': platform.machine(),
              'python': platform.python_version(), 'networkx': networkx.__version__,
              'compiler': subprocess.check_output([compiler, '--version'], text=True).splitlines()[0],
              'elapsed_seconds': round(time.monotonic() - started, 3),
              'checks': passed, 'bundle_sha256': plan['bundle_sha256'],
              'input_sha256': plan['input_sha256']}
    (output / 'QUICKCHECK_PASSED.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
