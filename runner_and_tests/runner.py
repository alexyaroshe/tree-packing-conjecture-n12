# block runner
# block -> worker -> check_a -> check_b -> hashes -> atomic completion record
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import uuid

COUNTS = (1, 1, 2, 3, 6, 11, 23, 47, 106, 235, 551)
STOP = threading.Event()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def seal(value):
    return {'record': value, 'sha256': hashlib.sha256(encoded(value)).hexdigest()}


def unseal(path):
    value = json.loads(Path(path).read_text())
    require(value == seal(value['record']), 'Completion record checksum mismatch: ' + str(path))
    return value['record']


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def cardinality(n):
    result = 1
    for count in COUNTS[:n - 1]:
        result *= count
    return result


def gated_context(build, gate, plan):
    require(os.uname().sysname == 'Linux', 'Production runner requires Linux')
    record = json.loads((build / 'BUILD.json').read_text())
    passed = json.loads((gate / 'GATE_PASSED').read_text())
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    token = hashlib.sha256(('native-build-v1\0' + os.uname().machine + '\0' + boot).encode()).hexdigest()
    require(passed['status'] == 'passed', 'Target gate has not passed')
    require(record['machine_token'] == passed['machine_token'] == token, 'Rebuild and gate after reboot')
    require(passed['build_manifest_sha256'] == digest(build / 'BUILD.json'), 'Build manifest changed')
    require(record['bundle_sha256'] == passed['bundle_sha256'] == digest(build / 'BUNDLE.json'), 'Bundle changed')
    require(record['artifacts'] == passed['binaries'], 'Gated binaries differ')
    require(record['sources'] == passed['sources'] and record['generated'] == passed['generated'], 'Gated input differs')
    for group in ('sources', 'artifacts', 'generated'):
        for name, expected in record[group].items():
            require(digest(build / name) == expected, 'Build hash mismatch: ' + name)
    for name, expected in passed['evidence'].items():
        require(digest(gate / name) == expected, 'Gate evidence hash mismatch: ' + name)
    return {'build_sha256': digest(build / 'BUILD.json'), 'gate_sha256': digest(gate / 'GATE_PASSED'),
            'runner_sha256': digest(__file__), 'plan_sha256': hashlib.sha256(encoded(plan)).hexdigest()}


class Interrupted(Exception):
    pass


def command_run(command, work, label, deadline):
    start = time.monotonic()
    with (work / (label + '.out')).open('wb') as out, (work / (label + '.err')).open('wb') as err:
        process = subprocess.Popen([str(item) for item in command], stdout=out, stderr=err,
                                   start_new_session=True, env={**os.environ, 'LC_ALL': 'C'})
        finished = threading.Event()
        def watch():
            while not finished.wait(0.1):
                if STOP.is_set() or time.monotonic() >= deadline:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            code = process.wait()
            if STOP.is_set() or time.monotonic() >= deadline:
                raise Interrupted('Time limit or stop request')
            require(code == 0, label + ' exited ' + str(code) + '; logs: ' + str(work))
        finally:
            finished.set()
            watcher.join()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            for stream in (out, err):
                stream.flush()
                os.fsync(stream.fileno())
    return time.monotonic() - start


def run_block(build, root, job, context, deadline, retain=False):
    require(2 <= job['n'] <= 12 and job['count'] > 0 and job['start'] >= 0
            and job['start'] + job['count'] <= cardinality(job['n']), 'Invalid block range')
    root.mkdir(parents=True, exist_ok=True)
    marker = root / 'complete.json'
    if marker.exists():
        record = unseal(marker)
        require(record['context'] == context and record['job'] == job, 'Resume context or block mismatch')
        for name, expected in record['logs'].items():
            require(digest(root / name) == expected, 'Resume log changed: ' + name)
        certificate = root / record['certificate']
        if record['retained']:
            require(certificate.is_file(), 'Retained certificate missing')
        if certificate.exists():
            require(digest(certificate) == record['certificate_sha256'], 'Resume certificate changed')
            if not record['retained']:
                certificate.unlink()
                sync_directory(certificate.parent)
        return record
    if STOP.is_set() or time.monotonic() >= deadline:
        raise Interrupted('No time for block')
    require(shutil.disk_usage(root).free > 5 * 1024 ** 3, 'Less than 5 GiB disk reserve')
    work = root / ('attempt-' + uuid.uuid4().hex)
    work.mkdir()
    started = time.monotonic()
    certificate = work / 'cert.bin'
    unsat = work / 'unsat.txt'
    common = ['--n', job['n'], '--trees', build / 'input.txt', '--cert', certificate]
    worker = [build / 'worker', *common, '--start', job['start'], '--limit', job['count'],
              '--seed', job['seed'], '--unsat', unsat]
    durations = {'worker': command_run(worker, work, 'worker', deadline)}
    lines = [line for line in (work / 'worker.out').read_text().splitlines() if line.startswith('LEDGER ')]
    require(len(lines) == 1, 'Missing or duplicate worker ledger')
    fields = dict(item.split('=', 1) for item in lines[0].split()[1:])
    require(int(fields['total']) == job['count'] and int(fields['UNSAT']) == 0,
            'Incomplete block or UNSAT candidate: ' + str(work))
    require(int(fields['stageA']) + int(fields['stageB']) == job['count'], 'Packing counts mismatch')
    require(int(fields['n']) == job['n'] and int(fields['seed']) == job['seed']
            and fields['res'] == '0' and fields['mod'] == '1' and fields['wlog'] == '1', 'Worker parameters mismatch')
    require(unsat.stat().st_size == 0, 'UNSAT evidence requires investigation')
    trace = 'SELECTION start={} limit={} first={}'.format(job['start'], job['count'], job['start'])
    require(trace in (work / 'worker.err').read_text().splitlines(), 'Worker range mismatch')
    require(certificate.stat().st_size == job['count'] * job['n'] * (job['n'] - 1) // 2,
            'Certificate byte count mismatch')
    with certificate.open('rb') as stream:
        os.fsync(stream.fileno())
    commands = {'worker': [str(item) for item in worker]}
    for checker in ('check_a', 'check_b'):
        command = [build / checker, *common, '--res', job['start'], '--mod', 1, '--limit', job['count']]
        durations[checker] = command_run(command, work, checker, deadline)
        expected = 'VERIFIED-PREFIX n={} slice={}/1: {} certificates, E1-E6 hold for the requested prefix'.format(
            job['n'], job['start'], job['count'])
        require((work / (checker + '.out')).read_text().strip() == expected, 'Checker coverage mismatch')
        commands[checker] = [str(item) for item in command]
    certificate_hash = digest(certificate)
    logs = {str(path.relative_to(root)): digest(path) for path in work.iterdir() if path != certificate}
    record = {'job': job, 'context': context, 'commands': commands, 'worker_ledger': fields,
              'certificate': str(certificate.relative_to(root)), 'certificate_sha256': certificate_hash,
              'retained': retain, 'logs': logs, 'durations': durations,
              'elapsed_seconds': time.monotonic() - started, 'completed_utc': time.time()}
    atomic(marker, seal(record))
    if not retain:
        certificate.unlink()
        sync_directory(work)
    return record
