# native build
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    args = parser.parse_args()
    require(platform.system() == 'Linux', 'Build on the target Linux machine')
    require(sys.flags.optimize == 0, 'Python assertion optimization must be disabled')
    compiler = shutil.which(args.cc)
    require(compiler is not None, 'GCC missing')
    for program in ('bash', 'gentreeg'):
        require(shutil.which(program) is not None, 'Missing build dependency: ' + program)
    import networkx
    token = machine_token()
    version = subprocess.check_output([compiler, '--version'], text=True)
    macros = subprocess.check_output([compiler, '-dM', '-E', '-x', 'c', '/dev/null'], text=True)
    require('__GNUC__' in macros and '__clang__' not in macros, 'A GCC target compiler is required')
    manifest = json.loads((ROOT / 'BUNDLE.json').read_text())
    mapping = manifest['runtime_sources']
    require(set(mapping) == REQUIRED, 'Source-only bundle file set mismatch')
    require(len(mapping) == len(manifest['files']) and set(mapping.values()) == set(manifest['files']),
            'Runtime source mapping mismatch')
    sources = {name: manifest['files'][relative] for name, relative in mapping.items()}
    for name, expected in manifest['files'].items():
        source = ROOT / name
        require(ROOT in source.parents and source.resolve() == source and source.is_file(),
                'Source path mismatch: ' + name)
        require(digest(source) == expected, 'Source digest mismatch: ' + name)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, relative in mapping.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copy2(ROOT / 'BUNDLE.json', output / 'BUNDLE.json')
    environment = dict(os.environ)
    environment.pop('PYTHONOPTIMIZE', None)
    environment.update({'RUN_PY': sys.executable, 'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C'})
    with (output / 'input-generation.log').open('w') as log:
        subprocess.run(['bash', 'gen_input.sh', 'input.txt'], cwd=output, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    require(digest(output / 'input.txt') == INPUT_SHA256, 'Generated input digest mismatch')
    with (output / 'input-audit.log').open('w') as log:
        subprocess.run([sys.executable, 'audit_input.py', 'input.txt', '12'], cwd=output,
                       env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    require('AUDIT PASSED for k=2..12' in (output / 'input-audit.log').read_text(),
            'Independent input audit success message missing')
    flags = ['-std=c11', '-D_POSIX_C_SOURCE=200809L', '-O3', '-march=native', '-Wall', '-Wextra', '-s']
    jobs = [('worker', 'worker.c'), ('check_a', 'check_a.c'), ('check_b', 'check_b.c')]
    commands = []
    with (output / 'build.log').open('w') as log:
        for binary, source in jobs:
            command = [compiler, *flags, source, '-o', binary]
            subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True)
            commands.append([Path(compiler).name, *command[1:]])
    artifacts = {binary: digest(output / binary) for binary, _ in jobs}
    record = {'machine_token': token, 'machine_token_method': 'sha256-linux-boot-id-v1',
              'system': platform.system(), 'machine': platform.machine(),
              'python': sys.version, 'networkx': networkx.__version__,
              'compiler': Path(compiler).name, 'compiler_version': version,
              'commands': commands, 'bundle_sha256': digest(output / 'BUNDLE.json'),
              'sources': sources, 'artifacts': artifacts,
              'generated': {'input.txt': INPUT_SHA256},
              'input_generation': ['bash', 'gen_input.sh', 'input.txt'],
              'input_audit': [Path(sys.executable).name, 'audit_input.py', 'input.txt', '12'],
              'scope': 'native build and input audit; target full gate pending'}
    (output / 'BUILD.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'build': str(output), 'artifacts': artifacts,
                      'generated': record['generated'], 'gate': 'pending'}))


if __name__ == '__main__':
    main()
