# SAT solver
import argparse
import os
import subprocess
import sys
import shutil
from collections import defaultdict
EXPECTED_TREE_COUNTS = {2: 1, 3: 1, 4: 2, 5: 3, 6: 6, 7: 11, 8: 23, 9: 47, 10: 106, 11: 235, 12: 551}

def read_trees(path, n):
    out, k = ({}, None)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            k = int(line[1:].strip())
            out.setdefault(k, [])
            continue
        out[k].append([int(x) - 1 for x in line.split()[1:]])
    for k in range(2, n + 1):
        if len(out[k]) != EXPECTED_TREE_COUNTS[k]:
            sys.exit(f'FATAL: {len(out[k])} trees at k={k}, expected {EXPECTED_TREE_COUNTS[k]}')
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, required=True)
    ap.add_argument('--trees', required=True)
    ap.add_argument('--seq', required=True, help='comma-separated tree indices for k=2..n')
    ap.add_argument('--work', default='/tmp')
    a = ap.parse_args()
    n = a.n
    idx = [int(t) for t in a.seq.split(',')]
    if len(idx) != n - 1:
        sys.exit(f'FATAL: --seq needs {n - 1} indices for k=2..{n}')
    trees = read_trees(a.trees, n)
    seq = {k: idx[k - 2] for k in range(2, n + 1)}
    pairs, eid = ([], {})
    for u in range(n):
        for v in range(u + 1, n):
            eid[u, v] = len(pairs)
            pairs.append((u, v))
    host = range(len(pairs))
    nv = 0

    def newvar():
        nonlocal nv
        nv += 1
        return nv
    y = {}
    x = {}
    for k in range(2, n + 1):
        for i in range(k):
            for v in range(n):
                y[k, i, v] = newvar()
        for e in host:
            x[k, e] = newvar()
    cls = []

    def exactly_one(lits):
        cls.append(list(lits))
        at_most_one(lits)

    def at_most_one(lits):
        for i in range(len(lits)):
            for j in range(i + 1, len(lits)):
                cls.append([-lits[i], -lits[j]])
    for k in range(2, n + 1):
        par = trees[k][seq[k]]
        for i in range(k):
            exactly_one([y[k, i, v] for v in range(n)])
        for v in range(n):
            at_most_one([y[k, i, v] for i in range(k)])
        for i in range(1, k):
            p = par[i - 1]
            for u in range(n):
                for v in range(n):
                    if u == v:
                        continue
                    e = eid[min(u, v), max(u, v)]
                    cls.append([-y[k, p, u], -y[k, i, v], x[k, e]])
    for e in host:
        exactly_one([x[k, e] for k in range(2, n + 1)])
    os.makedirs(a.work, exist_ok=True)
    tag = f'n{n}_' + '_'.join((str(i) for i in idx))
    cnf = os.path.join(a.work, f'job_{tag}.cnf')
    with open(cnf, 'w') as f:
        f.write(f'p cnf {nv} {len(cls)}\n')
        for c in cls:
            f.write(' '.join(map(str, c)) + ' 0\n')
    if not shutil.which('kissat'):
        sys.exit('FATAL: kissat not on PATH')
    r = subprocess.run(['kissat', '-q', cnf], capture_output=True, text=True)
    out = r.stdout
    if r.returncode == 20 or 's UNSATISFIABLE' in out:
        print(f'UNSAT seq={a.seq}  (cnf={cnf})')
        return 20
    if r.returncode != 10 and 's SATISFIABLE' not in out:
        print(f'kissat returned {r.returncode}\n{out[-2000:]}')
        return 2
    model = set()
    for line in out.splitlines():
        if line.startswith('v '):
            for t in line[2:].split():
                iv = int(t)
                if iv > 0:
                    model.add(iv)
    used = defaultdict(list)
    for k in range(2, n + 1):
        par = trees[k][seq[k]]
        img = {}
        for i in range(k):
            hits = [v for v in range(n) if y[k, i, v] in model]
            if len(hits) != 1:
                sys.exit(f'FATAL: tree {k} vertex {i} has {len(hits)} placements')
            img[i] = hits[0]
        if len(set(img.values())) != k:
            sys.exit(f'FATAL: tree {k} placement is not injective')
        for i in range(1, k):
            u, v = (img[par[i - 1]], img[i])
            used[eid[min(u, v), max(u, v)]].append(k)
    bad = [e for e in host if len(used[e]) != 1]
    if bad or len(used) != len(host):
        sys.exit(f'FATAL: not an exact decomposition; {len(bad)} edges misused')
    print(f'SAT seq={a.seq}  exact decomposition of {len(host)} edges, self-checked')
    return 0
if __name__ == '__main__':
    sys.exit(main())
