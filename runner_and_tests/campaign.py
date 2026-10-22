# campaign controller
# gated epoch -> portable ledger -> missing blocks -> dual checks -> commit
# drain -> finish active blocks -> snapshot -> new gated epoch -> resume
import argparse
import concurrent.futures
from collections import deque
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import tarfile
import time
import uuid
import zlib

import runner as core


def hash_value(value):
    return hashlib.sha256(core.encoded(value)).hexdigest()


def connect(state, readonly=False):
    path = Path(state) / 'ledger.sqlite'
    connection = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) if readonly else sqlite3.connect(path)
    connection.execute('PRAGMA busy_timeout=30000')
    if not readonly:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=FULL')
    return connection


def metadata(connection):
    return {key: json.loads(value) for key, value in connection.execute('SELECT key,value FROM metadata')}


def put(connection, key, value):
    connection.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, json.dumps(value)))


def block_count(plan):
    return (plan['total_sequences'] + plan['block_size'] - 1) // plan['block_size']


def block_job(plan, block):
    core.require(0 <= block < block_count(plan), 'Block outside manifest')
    start = block * plan['block_size']
    return {'n': plan['n'], 'start': start, 'count': min(plan['block_size'], plan['total_sequences'] - start),
            'seed': plan['solver_seed']}


def validate_plan(plan):
    core.require(plan['schema'] == 1 and 2 <= plan['n'] <= 12, 'Unsupported campaign')
    core.require(plan['total_sequences'] == core.cardinality(plan['n']), 'Wrong sequence-space cardinality')
    core.require(0 < plan['block_size'] <= 16777216, 'Invalid block size')
    core.require(0 < plan['max_block_seconds'] <= 3600,
                 'Invalid runtime bounds')
    core.require(plan['sample_stride'] > 0 and plan['disk_reserve_bytes'] >= 0, 'Invalid storage policy')
    core.require(math.gcd(plan['order_multiplier'], block_count(plan)) == 1, 'Schedule is not a permutation')
    core.require(plan['implementation'] == {'campaign.py': core.digest(__file__), 'runner.py': core.digest(core.__file__)},
                 'Controller implementation changed')


def initialize(state, plan):
    state.mkdir(parents=True, exist_ok=True)
    (state / 'epochs').mkdir(exist_ok=True)
    (state / 'work').mkdir(exist_ok=True)
    connection = connect(state)
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS epochs (id TEXT PRIMARY KEY,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS blocks (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id INTEGER NOT NULL UNIQUE,
            start INTEGER NOT NULL UNIQUE,
            count INTEGER NOT NULL,
            epoch TEXT NOT NULL,
            digest TEXT NOT NULL,
            body BLOB NOT NULL,
            previous TEXT NOT NULL,
            chain TEXT NOT NULL);
    ''')
    saved = metadata(connection)
    if saved:
        core.require(saved['plan'] == plan, 'Campaign manifest changed')
    else:
        with connection:
            put(connection, 'plan', plan)
            put(connection, 'campaign_id', hash_value(plan))
            put(connection, 'started_utc', time.time())
            put(connection, 'verified', 0)
            put(connection, 'completed_blocks', 0)
            put(connection, 'head', '0' * 64)
        core.atomic(state / 'plan.json', plan)
    return connection


def gated_epoch(build, gate, plan, state):
    context = core.gated_context(build, gate, plan)
    record = json.loads((build / 'BUILD.json').read_text())
    core.require(record['bundle_sha256'] == plan['bundle_sha256']
                 and record['generated']['input.txt'] == plan['input_sha256'], 'Campaign source or input mismatch')
    epoch = {'build_sha256': context['build_sha256'], 'gate_sha256': context['gate_sha256'],
             'bundle_sha256': record['bundle_sha256'], 'input_sha256': record['generated']['input.txt'],
             'binaries': record['artifacts'], 'machine_token': record['machine_token']}
    identity = hash_value(epoch)
    archive = state / 'epochs' / (identity + '.tar.gz')
    if not archive.exists():
        temporary = archive.with_suffix('.tmp')
        with tarfile.open(temporary, 'w:gz') as output:
            for name in ['BUILD.json', 'BUNDLE.json', *record['sources'], *record['artifacts'], 'input.txt']:
                output.add(build / name, arcname='build/' + name)
            output.add(gate, arcname='gate')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        temporary.replace(archive)
        core.sync_directory(archive.parent)
    epoch['evidence_sha256'] = core.digest(archive)
    return identity, epoch


def register_epoch(connection, identity, epoch, plan):
    core.require(hash_value({k: v for k, v in epoch.items() if k != 'evidence_sha256'}) == identity, 'Invalid epoch identity')
    core.require(epoch['bundle_sha256'] == plan['bundle_sha256'] and epoch['input_sha256'] == plan['input_sha256'],
                 'Epoch is for a different computation')
    body = core.encoded(epoch).decode()
    old = connection.execute('SELECT body FROM epochs WHERE id=?', (identity,)).fetchone()
    if old:
        core.require(old[0] == body, 'Epoch changed')
    else:
        with connection:
            connection.execute('INSERT INTO epochs VALUES (?,?)', (identity, body))


def validate_state(state, connection=None):
    owned = connection is None
    connection = connect(state, True) if owned else connection
    try:
        saved = metadata(connection)
        plan = saved['plan']
        validate_plan(plan)
        core.require(saved['campaign_id'] == hash_value(plan), 'Campaign identity changed')
        epochs = {key: json.loads(body) for key, body in connection.execute('SELECT id,body FROM epochs')}
        for identity, epoch in epochs.items():
            core.require(hash_value({k: v for k, v in epoch.items() if k != 'evidence_sha256'}) == identity, 'Epoch identity mismatch')
            core.require(epoch['bundle_sha256'] == plan['bundle_sha256'] and epoch['input_sha256'] == plan['input_sha256'], 'Epoch source mismatch')
            core.require(core.digest(state / 'epochs' / (identity + '.tar.gz')) == epoch['evidence_sha256'], 'Epoch evidence missing or changed')
        completed, total, head = set(), 0, '0' * 64
        for block, start, count, epoch, digest, raw, previous, chain in connection.execute(
                'SELECT block_id,start,count,epoch,digest,body,previous,chain FROM blocks ORDER BY sequence'):
            record = json.loads(zlib.decompress(raw))
            core.require(hash_value(record) == digest, 'Block record changed')
            job = block_job(plan, block)
            core.require(block not in completed and job == record['result']['job'], 'Duplicate or wrong block')
            core.require((start, count, epoch) == (job['start'], job['count'], record['epoch']), 'Block columns mismatch')
            core.require(epoch in epochs and record['campaign_id'] == saved['campaign_id'], 'Unknown block epoch')
            core.require(record['result']['context'] == {'campaign_id': saved['campaign_id'], 'epoch': epoch,
                         'build_sha256': epochs[epoch]['build_sha256'], 'gate_sha256': epochs[epoch]['gate_sha256']},
                         'Block execution binding mismatch')
            verify_result(record['result'], record['logs'], job)
            if record['sample_hex']:
                core.require(hashlib.sha256(bytes.fromhex(record['sample_hex'])).hexdigest() == record['sample_sha256'], 'Certificate sample changed')
            core.require(previous == head and chain == hash_value([head, block, epoch, digest]), 'Ledger chain broken')
            head = chain
            total += count
            completed.add(block)
        core.require((total, len(completed), head) == (saved['verified'], saved['completed_blocks'], saved['head']), 'Ledger totals or head mismatch')
        return completed, saved
    finally:
        if owned:
            connection.close()


def verify_result(result, logs, job):
    core.require(result['job'] == job, 'Worker interval mismatch')
    for name, expected in result['logs'].items():
        core.require(hashlib.sha256(logs[name].encode()).hexdigest() == expected, 'Worker log mismatch')
    ledger = result['worker_ledger']
    worker_logs = [value for name, value in logs.items() if name.endswith('/worker.out')]
    core.require(len(worker_logs) == 1, 'Worker log missing')
    lines = [line for line in worker_logs[0].splitlines() if line.startswith('LEDGER ')]
    core.require(len(lines) == 1 and dict(item.split('=', 1) for item in lines[0].split()[1:]) == ledger,
                 'Worker ledger changed')
    core.require(int(ledger['total']) == job['count'] and int(ledger['UNSAT']) == 0
                 and int(ledger['stageA']) + int(ledger['stageB']) == job['count'], 'Incomplete or negative block')
    core.require(int(ledger['n']) == job['n'] and int(ledger['seed']) == job['seed']
                 and ledger['res'] == '0' and ledger['mod'] == '1' and ledger['wlog'] == '1', 'Worker settings differ')
    for checker in ('check_a', 'check_b'):
        expected = 'VERIFIED-PREFIX n={} slice={}/1: {} certificates, E1-E6 hold for the requested prefix'.format(
            job['n'], job['start'], job['count'])
        verdicts = [value.strip() for name, value in logs.items() if name.endswith('/' + checker + '.out')]
        core.require(verdicts == [expected], 'Checker verdict mismatch')


def compute_block(build, state, plan, block, epoch_id, epoch):
    core.require(shutil.disk_usage(state).free > plan['disk_reserve_bytes'], 'Disk reserve reached')
    job = block_job(plan, block)
    folder = state / 'work' / (str(block) + '-' + uuid.uuid4().hex)
    context = {'campaign_id': hash_value(plan), 'epoch': epoch_id, 'build_sha256': epoch['build_sha256'],
               'gate_sha256': epoch['gate_sha256']}
    result = core.run_block(build, folder, job, context, time.monotonic() + plan['max_block_seconds'], True)
    logs = {name: (folder / name).read_text() for name in result['logs']}
    verify_result(result, logs, job)
    sample = b''
    if block % plan['sample_stride'] == 0 or block == block_count(plan) - 1:
        with (folder / result['certificate']).open('rb') as stream:
            sample = stream.read(min(1024, job['count']) * job['n'] * (job['n'] - 1) // 2)
    core.sync_directory(folder / Path(result['certificate']).parent)
    result['retained'] = False
    record = {'campaign_id': hash_value(plan), 'epoch': epoch_id, 'result': result, 'logs': logs,
              'sample_hex': sample.hex(), 'sample_sha256': hashlib.sha256(sample).hexdigest() if sample else None}
    return record, folder


def commit_block(connection, plan, block, record):
    job = block_job(plan, block)
    verify_result(record['result'], record['logs'], job)
    saved = metadata(connection)
    core.require(record['campaign_id'] == saved['campaign_id'], 'Wrong campaign result')
    core.require(connection.execute('SELECT 1 FROM epochs WHERE id=?', (record['epoch'],)).fetchone(), 'Unknown execution epoch')
    digest = hash_value(record)
    chain = hash_value([saved['head'], block, record['epoch'], digest])
    with connection:
        connection.execute('INSERT INTO blocks(block_id,start,count,epoch,digest,body,previous,chain) VALUES (?,?,?,?,?,?,?,?)',
                           (block, job['start'], job['count'], record['epoch'], digest,
                            zlib.compress(core.encoded(record)), saved['head'], chain))
        put(connection, 'verified', saved['verified'] + job['count'])
        put(connection, 'completed_blocks', saved['completed_blocks'] + 1)
        put(connection, 'head', chain)


def snapshot(state, output):
    core.require(not output.exists(), 'Snapshot destination exists')
    output.mkdir(parents=True)
    source = connect(state, True)
    target = sqlite3.connect(output / 'ledger.sqlite')
    try:
        source.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
        target.commit()
    finally:
        source.close()
        target.close()
    saved_connection = connect(output, True)
    saved = metadata(saved_connection)
    epochs = list(saved_connection.execute('SELECT id,body FROM epochs'))
    saved_connection.close()
    (output / 'epochs').mkdir()
    for identity, body in epochs:
        name = identity + '.tar.gz'
        shutil.copy2(state / 'epochs' / name, output / 'epochs' / name)
        core.require(core.digest(output / 'epochs' / name) == json.loads(body)['evidence_sha256'], 'Snapshot epoch copy failed')
    core.atomic(output / 'plan.json', saved['plan'])
    completed, checked = validate_state(output)
    hashes = {str(path.relative_to(output)): core.digest(path) for path in output.rglob('*') if path.is_file()}
    for name in hashes:
        with (output / name).open('rb') as stream:
            os.fsync(stream.fileno())
    core.sync_directory(output / 'epochs')
    result = {'files': hashes, 'campaign_id': checked['campaign_id'], 'verified': checked['verified'],
              'completed_blocks': len(completed), 'head': checked['head'], 'created_utc': time.time()}
    core.atomic(output / 'SNAPSHOT.json', result)
    return result


def execute(build, state, plan, connection, epoch_id, epoch, workers, stop_after=None):
    completed, initial = validate_state(state, connection)
    started = time.monotonic()
    progress = deque([(started, initial['verified'])])
    count = block_count(plan)
    order = ((plan['order_multiplier'] * i + plan['order_offset']) % count for i in range(count))
    missing = (block for block in order if block not in completed)
    pending = {}
    scheduled = 0
    fatal = None
    last_report = 0
    last_backup = time.monotonic()
    status = 'running'
    def report():
        saved = metadata(connection)
        now = time.monotonic()
        progress.append((now, saved['verified']))
        while len(progress) > 2 and progress[1][0] < now - 900:
            progress.popleft()
        rate = (saved['verified'] - progress[0][1]) / max(0.001, now - progress[0][0])
        value = {'status': status, 'campaign_id': saved['campaign_id'], 'epoch': epoch_id,
                 'verified_sequences': saved['verified'], 'total_sequences': plan['total_sequences'],
                 'completed_blocks': saved['completed_blocks'], 'total_blocks': count,
                 'percent': saved['verified'] / plan['total_sequences'] * 100,
                 'workers': workers, 'recent_verified_per_second': rate,
                 'estimated_remaining_seconds': (plan['total_sequences'] - saved['verified']) / rate if rate else None,
                 'heartbeat_utc': time.time(),
                 'active_blocks': [{'block': block, 'seconds': now - begun} for block, begun in pending.values()],
                 'error': fatal, 'ledger_head': saved['head']}
        core.atomic(state / 'status.json', value)
        print(json.dumps({k: value[k] for k in ('status', 'verified_sequences', 'completed_blocks', 'percent',
                                                'recent_verified_per_second', 'error')}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        def submit():
            nonlocal scheduled
            if core.STOP.is_set() or (state / 'DRAIN').exists():
                return
            if stop_after is not None and scheduled >= stop_after:
                return
            block = next(missing, None)
            if block is not None:
                future = executor.submit(compute_block, build, state, plan, block, epoch_id, epoch)
                pending[future] = (block, time.monotonic())
                scheduled += 1
        for _ in range(workers):
            submit()
        report()
        try:
            while pending:
                finished, _ = concurrent.futures.wait(pending, timeout=1,
                                                     return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    block, begun = pending.pop(future)
                    try:
                        record, folder = future.result()
                        commit_block(connection, plan, block, record)
                        shutil.rmtree(folder)
                    except core.Interrupted as error:
                        if not core.STOP.is_set():
                            fatal = 'Block {} exceeded its time limit: {}'.format(block, error)
                            core.STOP.set()
                    except Exception as error:
                        fatal = 'Block {}: {}'.format(block, repr(error))
                        core.STOP.set()
                    if fatal is None:
                        submit()
                now = time.monotonic()
                if now - last_report >= 10:
                    report()
                    last_report = now
                if now - last_backup >= 3600 and fatal is None:
                    backups = state / 'backups'
                    destination = backups / str(int(time.time()))
                    snapshot(state, destination)
                    for old in sorted(backups.iterdir())[:-2]:
                        shutil.rmtree(old)
                    last_backup = time.monotonic()
        except BaseException as error:
            core.STOP.set()
            fatal = repr(error)
    saved = metadata(connection)
    if saved['completed_blocks'] == count and saved['verified'] == plan['total_sequences']:
        validate_state(state, connection)
        status = 'completed'
    elif fatal:
        status = 'failed'
    else:
        status = 'paused'
    report()
    connection.execute('PRAGMA wal_checkpoint(FULL)')
    if fatal:
        raise RuntimeError(fatal)
    return status


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run')
    run.add_argument('--build', type=Path, required=True)
    run.add_argument('--gate', type=Path, required=True)
    run.add_argument('--plan', type=Path, required=True)
    run.add_argument('--state', type=Path, required=True)
    run.add_argument('--workers', type=int, required=True)
    run.add_argument('--resume', action='store_true')
    for action in ('status', 'pause', 'audit', 'backup'):
        command = sub.add_parser(action)
        command.add_argument('--state', type=Path, required=True)
        if action == 'backup':
            command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if args.action == 'status':
        connection = connect(state, True)
        saved = metadata(connection)
        connection.close()
        value = json.loads((state / 'status.json').read_text()) if (state / 'status.json').exists() else {}
        value.update(verified_sequences=saved['verified'], completed_blocks=saved['completed_blocks'],
                     ledger_head=saved['head'], heartbeat_age_seconds=time.time() - value.get('heartbeat_utc', 0))
        print(json.dumps(value, indent=2))
    elif args.action == 'pause':
        core.require((state / 'ledger.sqlite').exists(), 'Unknown campaign')
        core.atomic(state / 'DRAIN', {'requested_utc': time.time()})
        print('Drain requested; active blocks may finish and commit.')
    elif args.action == 'audit':
        complete, saved = validate_state(state)
        print(json.dumps({'status': 'passed', 'completed_blocks': len(complete), 'verified': saved['verified'], 'head': saved['head']}))
    elif args.action == 'backup':
        print(json.dumps(snapshot(state, args.output.resolve())))
    else:
        core.require(not sys.flags.optimize, 'Python assertions must remain enabled')
        plan = json.loads(args.plan.read_text())
        validate_plan(plan)
        core.require(1 <= args.workers <= (os.cpu_count() or 1), 'Worker count exceeds available CPUs')
        state.mkdir(parents=True, exist_ok=True)
        lock = (state / 'run.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        connection = initialize(state, plan)
        identity, epoch = gated_epoch(args.build.resolve(), args.gate.resolve(), plan, state)
        register_epoch(connection, identity, epoch, plan)
        validate_state(state, connection)
        if args.resume and (state / 'DRAIN').exists():
            (state / 'DRAIN').unlink()
            core.sync_directory(state)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: core.STOP.set())
        try:
            execute(args.build.resolve(), state, plan, connection, identity, epoch, args.workers)
        finally:
            connection.close()


if __name__ == '__main__':
    main()
