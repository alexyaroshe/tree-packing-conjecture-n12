# helper
import os
import pathlib
import subprocess
root = pathlib.Path(__file__).resolve().parents[1]
here = pathlib.Path(os.environ['RUN_TEST_WORK'])
here.mkdir(parents=True, exist_ok=True)
baseline = root
binary = os.environ.get('CHECKER_BINARY', str(root / 'check_a'))
reference = (baseline / 'input.txt').read_bytes()
index = (here / 'stars12.index').read_text().strip()
valid = (here / 'stars12.cert').read_bytes()
case = here / ('strict-' + pathlib.Path(binary).name + '.cert')
treefile = here / ('strict-' + pathlib.Path(binary).name + '.trees')
count = 0

def check(label, expected, record=valid, trees=reference, overrides=None):
    global count
    case.write_bytes(record)
    treefile.write_bytes(trees)
    args = {'--n': '12', '--trees': str(treefile), '--cert': str(case), '--res': index, '--mod': '1', '--limit': '1'}
    args.update(overrides or {})
    command = [binary] + [item for pair in args.items() for item in pair]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == (0 if expected else 1), (label, result.stderr)
    assert 'Sanitizer' not in result.stderr and 'runtime error:' not in result.stderr, (label, result.stderr)
    count += 1
check('valid offset', True)
check('missing record', False, record=b'')
check('truncated', False, record=valid[:-1])
check('whole-record excess', False, record=valid + valid)
check('trailing byte', False, record=valid + b'\x00')
check('wrong requested tree', False, overrides={'--res': '0'})
check('zero modulus', False, overrides={'--mod': '0'})
check('negative modulus', False, overrides={'--mod': '-1'})
check('overflow modulus', False, overrides={'--mod': str(2 ** 64)})
check('bad integer', False, overrides={'--res': index + 'x'})
check('out-of-space index', False, overrides={'--res': str(2 ** 64 - 1)})
check('unsupported order', False, overrides={'--n': '13'})
check('unknown option', False, overrides={'--explicit': '1'})
check('missing file', False, overrides={'--cert': str(here / 'absent.cert')})
check('directory certificate', False, overrides={'--cert': str(here)})
check('NUL tree', False, trees=reference + b'\x00\n')
check('missing reference', False, trees=reference.replace(b'0 1\n', b'', 1))
check('extra reference', False, trees=reference.replace(b'0 1\n', b'0 1\n0 1\n', 1))
check('malformed header', False, trees=reference.replace(b'# 2', b'# 2 x', 1))
check('duplicate section', False, trees=reference + b'\n# 2\n')
check('missing section', False, trees=reference.replace(b'# 2\n0 1\n', b'', 1))
check('invalid root marker', False, trees=reference.replace(b'0 1\n', b'1 1\n', 1))
check('self edge', False, trees=reference.replace(b'0 1\n', b'0 2\n', 1))
check('out-of-range parent', False, trees=reference.replace(b'0 1\n', b'0 3\n', 1))
check('trailing row text', False, trees=reference.replace(b'0 1\n', b'0 1oops\n', 1))
check('duplicate shape', False, trees=reference.replace(b'0 1 2 1\n', b'0 1 1 1\n', 1))
path = bytes([0, 3, 5, 1, 2, 4])
star = bytes([0, 1, 2, 3, 4, 5])
check('two-record exact slice', True, record=path + star, overrides={'--n': '4', '--res': '0', '--mod': '1', '--limit': '0'})
check('swapped sequence order', False, record=star + path, overrides={'--n': '4', '--res': '0', '--mod': '1', '--limit': '0'})
check('missing whole sequence', False, record=path, overrides={'--n': '4', '--res': '0', '--mod': '1', '--limit': '0'})
check('prefix beyond cardinality', False, record=star + star, overrides={'--n': '4', '--res': '1', '--mod': '1', '--limit': '2'})
print(f'PASS {count} strict parser tests: {pathlib.Path(binary).name}')
