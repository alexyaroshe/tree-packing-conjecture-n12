# campaign controls
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import campaign as c


def rejected(action):
    try:
        action()
    except Exception:
        return
    raise AssertionError('Negative control accepted')


def plan_for(build):
    return {'schema': 1, 'n': 8, 'total_sequences': 9108, 'block_size': 317,
            'solver_seed': 20260915, 'order_multiplier': 104729, 'order_offset': 17,
            'max_block_seconds': 60, 'disk_reserve_bytes': 0,
            'sample_stride': 1, 'bundle_sha256': c.core.digest(build / 'BUNDLE.json'),
            'input_sha256': c.core.digest(build / 'input.txt'),
            'implementation': {'campaign.py': c.core.digest(c.__file__), 'runner.py': c.core.digest(c.core.__file__)}}


def epoch_for(state, plan, label):
    epoch = {'build_sha256': hashlib.sha256(label.encode()).hexdigest(),
             'gate_sha256': hashlib.sha256((label + '-gate').encode()).hexdigest(),
             'bundle_sha256': plan['bundle_sha256'], 'input_sha256': plan['input_sha256'],
             'machine_token': label, 'simulation': True}
    identity = c.hash_value(epoch)
    archive = state / 'epochs' / (identity + '.tar.gz')
    archive.write_bytes(('simulated gate evidence: ' + label).encode())
    epoch['evidence_sha256'] = c.core.digest(archive)
    return identity, epoch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build, output = args.build.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = plan_for(build)
    c.validate_plan(plan)
    old = output / 'old-server'
    connection = c.initialize(old, plan)
    epoch1, first = epoch_for(old, plan, 'server-one')
    c.register_epoch(connection, epoch1, first, plan)
    assert c.execute(build, old, plan, connection, epoch1, first, 2, stop_after=5) == 'paused'
    with connection:
        c.put(connection, 'started_utc', 1)
    prior = c.metadata(connection)
    assert prior['completed_blocks'] == 5
    backup = output / 'transfer'
    c.snapshot(old, backup)
    connection.close()
    old.rename(output / 'old-server-preserved')
    moved = output / 'new-server'
    shutil.copytree(backup, moved)
    relocated_build = output / 'relocated-build'
    relocated_build.mkdir()
    for name in ('worker', 'check_a', 'check_b', 'input.txt', 'BUNDLE.json', 'reference_checker.py'):
        shutil.copy2(build / name, relocated_build / name)
    connection = c.initialize(moved, plan)
    epoch2, second = epoch_for(moved, plan, 'server-two-new-native-build')
    c.register_epoch(connection, epoch2, second, plan)
    assert c.metadata(connection)['started_utc'] == prior['started_utc'] == 1
    assert c.execute(relocated_build, moved, plan, connection, epoch2, second, 3) == 'completed'
    completed, saved = c.validate_state(moved, connection)
    assert len(completed) == 29 and saved['verified'] == 9108
    assert dict(connection.execute('SELECT epoch,count(*) FROM blocks GROUP BY epoch')) == {epoch1: 5, epoch2: 24}
    checked = 0
    (output / 'samples').mkdir()
    first_record = None
    for block, raw in connection.execute('SELECT block_id,body FROM blocks'):
        record = json.loads(c.zlib.decompress(raw))
        first_record = (block, record)
        job = record['result']['job']
        sample = output / 'samples' / (str(block) + '.cert')
        sample.write_bytes(bytes.fromhex(record['sample_hex']))
        subprocess.run([sys.executable, str(build / 'reference_checker.py'), '--n', '8', '--trees', str(build / 'input.txt'),
                        '--cert', str(sample), '--res', str(job['start']), '--mod', '1', '--limit', str(job['count'])],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        checked += job['count']
    assert checked == 9108
    rejected(lambda: c.commit_block(connection, plan, *first_record))
    assert c.metadata(connection)['verified'] == 9108
    with connection:
        connection.execute('UPDATE blocks SET count=count+1 WHERE block_id=0')
    rejected(lambda: c.validate_state(moved, connection))
    with connection:
        connection.execute('UPDATE blocks SET count=count-1 WHERE block_id=0')
    archive = moved / 'epochs' / (epoch1 + '.tar.gz')
    archive.rename(archive.with_suffix('.hidden'))
    rejected(lambda: c.validate_state(moved, connection))
    archive.with_suffix('.hidden').rename(archive)
    rejected(lambda: c.register_epoch(connection, epoch2, {**second, 'input_sha256': 'changed'}, plan))
    connection.close()
    crash = subprocess.run([sys.executable, '-c',
        'import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); c.execute("BEGIN IMMEDIATE"); c.execute("UPDATE metadata SET value=999999 WHERE key=\'verified\'"); os._exit(13)',
        str(moved / 'ledger.sqlite')])
    assert crash.returncode == 13
    assert c.validate_state(moved)[1]['verified'] == 9108
    drained = output / 'drain'
    connection = c.initialize(drained, plan)
    epoch3, third = epoch_for(drained, plan, 'drain-test')
    c.register_epoch(connection, epoch3, third, plan)
    original = c.compute_block
    def draining(*parameters):
        record = original(*parameters)
        c.core.atomic(drained / 'DRAIN', {'control': True})
        return record
    c.compute_block = draining
    try:
        assert c.execute(build, drained, plan, connection, epoch3, third, 3) == 'paused'
    finally:
        c.compute_block = original
    assert 1 <= c.metadata(connection)['completed_blocks'] <= 3
    (drained / 'DRAIN').unlink()
    assert c.execute(build, drained, plan, connection, epoch3, third, 3) == 'completed'
    assert c.validate_state(drained, connection)[1]['verified'] == 9108
    connection.close()
    real_total, real_size = 5875518611160, 4194304
    real_count = (real_total + real_size - 1) // real_size
    assert math.gcd(104729, real_count) == 1
    assert (real_count - 1) * real_size < real_total <= real_count * real_size
    result = {'status': 'passed', 'full_n8_sequences': checked, 'blocks': 29,
              'simulated_migration_preserved_blocks': 5, 'new_epoch_blocks': 24,
              'full_n12_blocks': real_count, 'last_n12_block_count': real_total - (real_count - 1) * real_size,
              'implementation': plan['implementation'],
              'controls': ['copied snapshot and relocated binaries', 'epoch change without recompute',
                           'duplicate credit rejected', 'corrupted count rejected', 'missing epoch rejected',
                           'wrong input rejected', 'transaction crash rollback', 'graceful drain and resume',
                           'resume with an old campaign start time',
                           'both C checkers and Python on full n8', 'full-space partition and schedule bijection']}
    c.core.atomic(output / 'TEST_PASSED.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
