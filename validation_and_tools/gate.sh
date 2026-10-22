#!/bin/bash
# gate
set -uo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
W="${1:-$HERE/logs/gate}"
PY="${RUN_PY:-$(command -v python3)}"
mkdir -p "$W"
FAILED=0

say()  { echo; echo "=== $*"; }
ok()   { echo "  PASS  $*"; }
bad()  { echo "  FAIL  $*"; FAILED=1; }
assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1 = $2"; else bad "$1: got '$2', want '$3'"; fi
}
field() { tr ' ' '\n' <<<"$1" | sed -n "s/^$2=//p" | head -1; }

[ -x ./worker ] || { echo "FATAL: ./worker not built"; exit 2; }
[ -x "$PY" ] || { echo "FATAL: $PY missing"; exit 2; }
[ -f input.txt ] || { echo "FATAL: input.txt missing (run gen_input.sh)"; exit 2; }

say "G1  enumeration audit (A000055 / non-isomorphic / vs networkx)"
if "$PY" audit_input.py input.txt 12 > "$W/g1.log" 2>&1; then
  ok "input.txt audited for k=2..12"
else
  bad "tree audit"; tail -5 "$W/g1.log"
fi

say "G2  sequence-space anchors"
for spec in "9:428076" "10:45376056" "11:10663373160"; do
  n="${spec%%:*}"; want="${spec##*:}"
  got=$("$PY" - "$n" <<'EOF'
import sys
A={2:1,3:1,4:2,5:3,6:6,7:11,8:23,9:47,10:106,11:235,12:551}
n=int(sys.argv[1]); t=1
for k in range(2,n+1): t*=A[k]
print(t)
EOF
)
  assert_eq "sequences at n=$n" "$got" "$want"
done

say "G3  exactness identity sum(k-1) = C(n,2)"
for n in 9 10 11 12; do
  got=$("$PY" -c "n=$n; print(sum(k-1 for k in range(2,n+1)), n*(n-1)//2)")
  a="${got%% *}"; b="${got##* }"
  assert_eq "n=$n" "$a" "$b"
done

say "G4  FULL n=9 VERIFICATION — full n=9 sweep (C engine, WLOG on)"
./worker --n 9 --trees input.txt --cert "$W/n9.cert" --unsat "$W/n9.unsat" > "$W/g4.led" 2> "$W/g4.err"
L=$(grep '^LEDGER' "$W/g4.led")
echo "  $L"
assert_eq "n=9 total"      "$(field "$L" total)" "428076"
assert_eq "n=9 unsat"      "$(field "$L" UNSAT)" "0"
assert_eq "n=9 escalated"  "$(field "$L" escalated)" "0"
SA=$(field "$L" stageA)
assert_eq "n=9 solved by stage A" "$SA" "428076"
assert_eq "n=9 unsat file empty"  "$(wc -l < "$W/n9.unsat" | tr -d ' ')" "0"

say "G5  the WLOG lemma at scale — same sweep with --no-wlog"
./worker --n 9 --trees input.txt --no-wlog > "$W/g5.led" 2> "$W/g5.err"
L5=$(grep '^LEDGER' "$W/g5.led")
echo "  $L5"
assert_eq "n=9 no-wlog total" "$(field "$L5" total)" "428076"
assert_eq "n=9 no-wlog unsat" "$(field "$L5" UNSAT)" "0"

say "G6  independent second engine — full n=9 Python twin"
"$PY" reference_solver.py --n 9 --trees input.txt --work "$W/twin-sat" > "$W/g6.led" 2> "$W/g6.err"
L6=$(grep '^LEDGER-TWIN' "$W/g6.led")
echo "  $L6"
assert_eq "twin n=9 total"      "$(field "$L6" total)" "428076"
assert_eq "twin n=9 packed"     "$(field "$L6" packed)" "428076"
assert_eq "twin n=9 UNSAT"      "$(field "$L6" UNSAT)" "0"
assert_eq "twin n=9 unresolved" "$(field "$L6" unresolved)" "0"
TE=$(field "$L6" escalated)
TEP=$(field "$L6" escalated_packed)
assert_eq "twin escalations all resolved by the SAT path" "$TE" "$TEP"
echo "  note: the twin escalated $TE of 428076 sequences to kissat; every one came back SAT."

say "G7  certificate verification — 100% of the n=9 certificates"
if "$PY" reference_checker.py --n 9 --trees input.txt --cert "$W/n9.cert" > "$W/g7.log" 2>&1; then
  ok "$(tail -1 "$W/g7.log")"
else
  bad "certificate verification"; tail -5 "$W/g7.log"
fi

say "G8  NEGATIVE CONTROLS — the checker must be able to fail"
cp "$W/n9.cert" "$W/bad_flip.cert"
"$PY" - "$W/bad_flip.cert" <<'EOF'
import sys
p=sys.argv[1]
with open(p,'r+b') as f:
    f.seek(17); b=f.read(1)[0]; f.seek(17); f.write(bytes([(b+1)%36]))
EOF
if "$PY" reference_checker.py --n 9 --trees input.txt --cert "$W/bad_flip.cert" > "$W/g8a.log" 2>&1
then bad "(a) verifier ACCEPTED a corrupted certificate"; else ok "(a) corrupted byte rejected: $(head -1 "$W/g8a.log")"; fi
cp "$W/n9.cert" "$W/bad_swap.cert"
"$PY" - "$W/bad_swap.cert" <<'EOF'
import sys
p=sys.argv[1]
with open(p,'r+b') as f:
    d=bytearray(f.read(36)); d[0],d[35]=d[35],d[0]; f.seek(0); f.write(d)
EOF
if "$PY" reference_checker.py --n 9 --trees input.txt --cert "$W/bad_swap.cert" > "$W/g8b.log" 2>&1
then bad "(b) verifier ACCEPTED a permuted certificate"; else ok "(b) permuted edges rejected: $(head -1 "$W/g8b.log")"; fi
head -c $(( $(wc -c < "$W/n9.cert") - 36 )) "$W/n9.cert" > "$W/bad_short.cert"
if "$PY" reference_checker.py --n 9 --trees input.txt --cert "$W/bad_short.cert" > "$W/g8c.log" 2>&1
then bad "(c) verifier ACCEPTED a short certificate file"; else ok "(c) missing certificate rejected: $(head -1 "$W/g8c.log")"; fi

say "G9  SAT packing reconstruction on K_7"
"$PY" sat_solver.py --n 7 --trees input.txt --seq 0,0,0,0,0,0 --work "$W/sat-positive" > "$W/g9sat.log" 2>&1
if grep -q '^SAT ' "$W/g9sat.log"; then
  ok "SAT path on K_7: $(head -1 "$W/g9sat.log")"
else
  bad "SAT path failed on a satisfiable instance"; cat "$W/g9sat.log"
fi

echo
if [ "$FAILED" = 0 ]; then
  echo "GATE PASSED  ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))"
  echo "  n=9 full verification: 428,076 sequences, 0 escalations, 0 unsat,"
  echo "  verified by two independent engines and a code-disjoint certificate checker."
  exit 0
else
  echo "GATE FAILED — no claim may be made and no compute may be spent."
  exit 1
fi
