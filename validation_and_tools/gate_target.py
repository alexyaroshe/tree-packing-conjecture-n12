# target gate
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

INPUT_SHA256 = '745bd6734f79983cf21976fb980cf5cb8b9b5b60ccf39a1b4e6653fedb576b06'
REQUIRED = {'worker.c', 'check_a.c', 'check_b.c', 'reference_solver.py', 'sat_solver.py',
            'reference_checker.py', 'audit_input.py', 'build.py',
            'gate_target.py', 'gate.sh', 'gen_input.sh', 'tests/test_checker.py',
            'tests/test_strict.py', 'tests/test_warm.py'}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def machine_token():
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(bool(boot_id), 'Linux boot identifier missing')
    value = 'native-build-v1\0' + platform.machine() + '\0' + boot_id
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def check_build(build, record):
    require(digest(build / 'BUNDLE.json') == record['bundle_sha256'], 'Bundle manifest changed')
    require(record['generated'] == {'input.txt': INPUT_SHA256}, 'Generated input binding mismatch')
    manifest = json.loads((build / 'BUNDLE.json').read_text())
    mapping = manifest['runtime_sources']
    require(set(mapping) == REQUIRED
            and len(mapping) == len(manifest['files'])
            and set(mapping.values()) == set(manifest['files']), 'Runtime source mapping mismatch')
    sources = {name: manifest['files'][relative] for name, relative in mapping.items()}
    require(record['sources'] == sources,
            'Build source manifest mismatch')
    require(set(record['artifacts']) == {'worker', 'check_a', 'check_b'}, 'Build artifact set mismatch')
    for name, expected in {**record['sources'], **record['artifacts'], **record['generated']}.items():
        require(digest(build / name) == expected, 'Build digest mismatch: ' + name)


def run(command, log, cwd, environment):
    with log.open('w') as output:
        result = subprocess.run([str(item) for item in command], stdout=output,
                                stderr=subprocess.STDOUT, cwd=cwd, env=environment)
    require(result.returncode == 0, 'Gate command failed; see ' + str(log))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(platform.system() == 'Linux', 'Run the gate on the target Linux machine')
    require(sys.flags.optimize == 0, 'Python assertion optimization must be disabled')
    build = args.build.resolve()
    build_manifest_sha256 = digest(build / 'BUILD.json')
    record = json.loads((build / 'BUILD.json').read_text())
    require(record['system'] == 'Linux' and record['machine'] == platform.machine()
            and record['machine_token_method'] == 'sha256-linux-boot-id-v1'
            and record['machine_token'] == machine_token(),
            'Build and gate must run on the same target machine and boot; rebuild after reboot')
    check_build(build, record)
    require(digest(Path(__file__).resolve()) == record['sources']['gate_target.py'], 'Gate entrypoint changed')
    for program in ('bash', 'kissat'):
        require(shutil.which(program) is not None, 'Missing gate dependency: ' + program)
    import networkx
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment.pop('PYTHONOPTIMIZE', None)
    environment.update({'RUN_PY': sys.executable,
                        'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C'})
    full = output / 'full-gate'
    run(['bash', build / 'gate.sh', full], output / 'full-gate.log', build, environment)
    require('GATE PASSED  (' in (output / 'full-gate.log').read_text(), 'Full-gate success message missing')
    checks = []
    for binary in ('check_a', 'check_b'):
        log = output / (binary + '-n9.log')
        run([build / binary, '--n', '9', '--trees', build / 'input.txt', '--cert', full / 'n9.cert'],
            log, build, environment)
        require('428076 certificates, E1-E6 hold' in log.read_text(), 'Compiled checker count mismatch')
        work = output / 'tests' / binary
        work.mkdir(parents=True)
        test_environment = {**environment, 'CHECKER_BINARY': str(build / binary),
                            'RUN_TEST_WORK': str(work), 'RUN_N9_CERT': str(full / 'n9.cert')}
        for script, expected in (('test_checker.py', 'PASS 1596 differential cases'),
                                 ('test_strict.py', 'PASS 30 strict parser tests'),
                                 ('test_warm.py', 'PASS 668 warmed-cache differential controls')):
            test_log = work / (script + '.log')
            run([sys.executable, build / 'tests' / script], test_log, build, test_environment)
            require(expected in test_log.read_text(), 'Checker test count mismatch: ' + str(test_log))
        checks.append({'binary': binary, 'n9_certificates': 428076,
                       'differential': 1596, 'strict': 30, 'warmed': 668})
    check_build(build, record)
    require(digest(build / 'BUILD.json') == build_manifest_sha256, 'Build manifest changed during gate')
    artifacts = {str(path.relative_to(output)): digest(path)
                 for path in sorted(output.rglob('*')) if path.is_file()}
    result = {'status': 'passed', 'machine_token': record['machine_token'],
              'machine_token_method': record['machine_token_method'], 'machine': platform.machine(),
              'build_manifest_sha256': build_manifest_sha256,
              'bundle_sha256': record['bundle_sha256'], 'binaries': record['artifacts'],
              'sources': record['sources'], 'generated': record['generated'],
              'python': sys.version, 'networkx': networkx.__version__,
              'checks': checks, 'evidence': artifacts,
              'scope': 'target gate and checker controls; no exhaustive campaign'}
    (output / 'GATE_PASSED').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'gate': str(output / 'GATE_PASSED'), 'binaries': record['artifacts']}))


if __name__ == '__main__':
    main()
