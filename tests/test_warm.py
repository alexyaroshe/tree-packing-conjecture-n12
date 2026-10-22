# helper
import importlib.util
import itertools
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
here = Path(os.environ['RUN_TEST_WORK'])
here.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('oracle', root / 'reference_checker.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
refs = oracle.load_reference_trees(root / 'input.txt', 9)
binary = os.environ.get('CHECKER_BINARY', str(root / 'check_a'))
pairs = list(itertools.combinations(range(9), 2))
with Path(os.environ['RUN_N9_CERT']).open('rb') as cert_source:
    source = cert_source.read(72)
case = here / 'warm.cert'

def valid(record, index):
    if sorted(record) != list(range(36)):
        return False
    seq = {}
    for k in range(2, 10):
        seq[k] = index % len(refs[k])
        index //= len(refs[k])
    offset = 0
    for k in range(9, 1, -1):
        adj = {}
        for e in record[offset:offset + k - 1]:
            u, v = pairs[e]
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, []).append(u)
        offset += k - 1
        if len(adj) != k:
            return False
        seen = set()
        pending = [next(iter(adj))]
        while pending:
            v = pending.pop()
            if v not in seen:
                seen.add(v)
                pending.extend(adj[v])
        if len(seen) != k or oracle.ahu_unrooted(adj, sorted(adj)) != refs[k][seq[k]]:
            return False
    return True

def check(record, label):
    case.write_bytes(source[:36] + record)
    result = subprocess.run([binary, '--n', '9', '--trees', str(root / 'input.txt'), '--cert', str(case), '--limit', '2'], capture_output=True, text=True)
    expected = valid(record, 1)
    assert result.returncode == (0 if expected else 1), (label, expected, result.stderr)
    assert 'Sanitizer' not in result.stderr and 'runtime error:' not in result.stderr, (label, result.stderr)
    return result
result = check(source[36:], 'valid pair')
assert result.returncode == 0
assert 'CACHE hits=0 ' not in result.stderr, result.stderr
checks = 1
for i, j in itertools.combinations(range(36), 2):
    record = bytearray(source[36:])
    record[i], record[j] = (record[j], record[i])
    check(record, f'swap {i} {j}')
    checks += 1
check(source[:36], 'cached wrong sequence')
checks += 1
for i in range(36):
    record = bytearray(source[36:])
    record[i] = record[(i + 1) % 36]
    check(record, f'duplicate {i}')
    checks += 1
print(f'PASS {checks} warmed-cache differential controls: {Path(binary).name}')
