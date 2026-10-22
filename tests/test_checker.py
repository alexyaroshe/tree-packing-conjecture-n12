# helper
import importlib.util
import itertools
import os
import pathlib
import random
import subprocess
import sys
sys.dont_write_bytecode = True
root = pathlib.Path(__file__).resolve().parents[1]
here = pathlib.Path(os.environ['RUN_TEST_WORK'])
here.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('oracle', root / 'reference_checker.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
refs = oracle.load_reference_trees(root / 'input.txt', 12)
binary = os.environ.get('CHECKER_BINARY', str(root / 'check_a'))
trees = str(root / 'input.txt')
case = here / 'case.cert'
checks = 0

def agrees(n, index, record):
    global checks
    pairs = list(itertools.combinations(range(n), 2))
    good = len(record) == len(pairs) and sorted(record) == list(range(len(pairs)))
    seq = {}
    x = index
    for k in range(2, n + 1):
        seq[k] = x % len(refs[k])
        x //= len(refs[k])
    offset = 0
    if good:
        for k in range(n, 1, -1):
            adj = {}
            for edge in record[offset:offset + k - 1]:
                u, v = pairs[edge]
                adj.setdefault(u, []).append(v)
                adj.setdefault(v, []).append(u)
            offset += k - 1
            if len(adj) != k:
                good = False
                break
            seen = set()
            stack = [next(iter(adj))]
            while stack:
                v = stack.pop()
                if v not in seen:
                    seen.add(v)
                    stack.extend(adj[v])
            if len(seen) != k or oracle.ahu_unrooted(adj, sorted(adj)) != refs[k][seq[k]]:
                good = False
                break
    case.write_bytes(bytes(record))
    result = subprocess.run([binary, '--n', str(n), '--trees', trees, '--cert', str(case), '--res', str(index), '--mod', '1', '--limit', '1'], capture_output=True, text=True)
    assert result.returncode == (0 if good else 1), (n, index, record, good, result.stderr)
    checks += 1
    return good
valid = 0
for record in itertools.permutations(range(6)):
    for index in range(2):
        valid += agrees(4, index, record)
print(f'PASS n4 all 720 edge permutations x 2 shapes: {valid} valid cases', flush=True)
rng = random.Random(9122026)
for _ in range(100):
    record = list(range(10))
    rng.shuffle(record)
    agrees(5, rng.randrange(6), record)
print('PASS n5 100 shuffled decompositions', flush=True)
for n in range(2, 13):
    pairs = list(itertools.combinations(range(n), 2))
    record = []
    index = 0
    multiplier = 1
    for k in range(2, n + 1):
        adj = {v: [0] for v in range(1, k)}
        adj[0] = list(range(1, k))
        shape = oracle.ahu_unrooted(adj, list(range(k)))
        index += refs[k].index(shape) * multiplier
        multiplier *= len(refs[k])
    for k in range(n, 1, -1):
        record.extend((pairs.index((n - k, v)) for v in range(n - k + 1, n)))
    assert agrees(n, index, record)
    if n == 12:
        (here / 'stars12.cert').write_bytes(bytes(record))
        (here / 'stars12.index').write_text(str(index) + '\n')
    agrees(n, index, record[:-1])
    agrees(n, index, record + [0])
    agrees(n, index, [255] + record[1:])
    if len(record) > 1:
        agrees(n, index, [record[1]] + record[1:])
print('PASS n2..12 stars, truncated, trailing, out-of-range, duplicate', flush=True)
agrees(5, 0, [0, 1, 4, 9, 2, 3, 5, 6, 7, 8])
agrees(12, 0, list((here / 'stars12.cert').read_bytes()))
print('PASS disconnected triangle-plus-edge, incorrect requested tree', flush=True)
print(f'PASS {checks} differential cases', flush=True)
