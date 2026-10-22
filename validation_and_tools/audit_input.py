# helper
import sys
from collections import defaultdict
EXPECTED_TREE_COUNTS = {2: 1, 3: 1, 4: 2, 5: 3, 6: 6, 7: 11, 8: 23, 9: 47, 10: 106, 11: 235, 12: 551}

def ahu(edges, k):
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    verts = list(adj) if k > 1 else [0]
    deg = {v: len(adj[v]) for v in verts}
    remaining = set(verts)
    leaves = [v for v in remaining if deg[v] <= 1]
    while len(remaining) > 2:
        nxt = []
        for v in leaves:
            remaining.discard(v)
            for w in adj[v]:
                if w in remaining:
                    deg[w] -= 1
                    if deg[w] == 1:
                        nxt.append(w)
        leaves = nxt
    centre = sorted(remaining)

    def code(v, parent):
        kids = sorted((code(w, v) for w in adj[v] if w != parent))
        return '(' + ''.join(kids) + ')'
    if len(centre) == 1:
        return code(centre[0], None)
    a, b = centre
    return min(code(a, b) + code(b, a), code(b, a) + code(a, b))

def parse_tree_file(path):
    out = defaultdict(list)
    k = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                k = int(line[1:].strip())
                continue
            vals = [int(x) for x in line.split()]
            if k is None:
                sys.exit('FATAL: tree data before any header')
            if len(vals) != k:
                sys.exit(f'FATAL: k={k} line has {len(vals)} entries: {line}')
            if vals[0] != 0:
                sys.exit(f'FATAL: k={k} root parent is {vals[0]} not 0')
            par = []
            for i in range(1, k):
                p = vals[i] - 1
                if not 0 <= p < i:
                    sys.exit(f'FATAL: k={k} vertex {i} parent {p} not < {i}')
                par.append(p)
            out[k].append(par)
    return out

def main():
    if len(sys.argv) != 3:
        sys.exit('usage: audit_input.py input.txt NMAX')
    path, nmax = (sys.argv[1], int(sys.argv[2]))
    data = parse_tree_file(path)
    try:
        import networkx as nx
    except ImportError:
        sys.exit('FATAL: networkx missing')
    failures = 0
    for k in range(2, nmax + 1):
        got = data.get(k, [])
        if len(got) != EXPECTED_TREE_COUNTS[k]:
            print(f'FAIL k={k}: {len(got)} trees, A000055 says {EXPECTED_TREE_COUNTS[k]}')
            failures += 1
            continue
        file_forms = [ahu([(i + 1, p) for i, p in enumerate(par)], k) for par in got]
        if len(set(file_forms)) != len(file_forms):
            print(f'FAIL k={k}: only {len(set(file_forms))} distinct of {len(file_forms)} — the file contains isomorphic duplicates')
            failures += 1
            continue
        nx_forms = {ahu(list(tree.edges()), k) for tree in nx.nonisomorphic_trees(k)}
        if set(file_forms) != nx_forms:
            only_file = set(file_forms) - nx_forms
            only_nx = nx_forms - set(file_forms)
            print(f'FAIL k={k}: set mismatch vs networkx ({len(only_file)} only in file, {len(only_nx)} only in networkx)')
            failures += 1
            continue
        print(f'  k={k:2d}: {len(got):5d} trees — count=A000055, pairwise non-isomorphic, set matches networkx')
    if failures:
        print(f'AUDIT FAILED ({failures} sizes)')
        return 1
    print(f'AUDIT PASSED for k=2..{nmax}')
    return 0
if __name__ == '__main__':
    sys.exit(main())
