#!/bin/bash
# input generator
set -euo pipefail
OUT="${1:-input.txt}"
PY="${RUN_PY:-python3}"
command -v gentreeg >/dev/null || { echo 'FATAL: gentreeg missing' >&2; exit 2; }
command -v "$PY" >/dev/null || { echo 'FATAL: Python missing' >&2; exit 2; }
[ ! -e "$OUT" ] || { echo 'FATAL: output already exists' >&2; exit 2; }
TEMP="$(mktemp "${OUT}.tmp.XXXXXX")"
trap 'rm -f "$TEMP"' EXIT
for ((k=2; k<=12; k++)); do
    echo "# $k" >> "$TEMP"
    gentreeg -p -q "$k" >> "$TEMP"
done
"$PY" - "$TEMP" <<'PY'
import hashlib
from pathlib import Path
import sys

raw = Path(sys.argv[1]).read_bytes()
counts = {}
section = None
for line in raw.decode('ascii').splitlines():
    if line.startswith('# '):
        section = int(line[2:])
        if section in counts:
            raise SystemExit('FATAL: duplicate input section')
        counts[section] = 0
    else:
        if section is None or not line:
            raise SystemExit('FATAL: malformed input section')
        counts[section] += 1
expected = dict(enumerate((1, 1, 2, 3, 6, 11, 23, 47, 106, 235, 551), start=2))
if counts != expected:
    raise SystemExit('FATAL: generated counts differ from A000055')
value = hashlib.sha256(raw).hexdigest()
if value != '745bd6734f79983cf21976fb980cf5cb8b9b5b60ccf39a1b4e6653fedb576b06':
    raise SystemExit('FATAL: generated input SHA-256 mismatch; gentreeg ordering or content differs: ' + value)
print('INPUT PASSED: sizes 2..12; SHA-256 ' + value)
PY
mv "$TEMP" "$OUT"
trap - EXIT
