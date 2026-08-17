#!/usr/bin/env bash
# Re-derive every frozen vector's body_hash WITHOUT chirograph code.
#
# The point: `tests/test_vectors.py` checks that the implementation reproduces
# the vectors, but the vectors were generated *by* that implementation, so on
# their own the two only prove self-consistency. This script closes the loop
# using coreutils — it takes the literal `canonical` string out of the vectors
# file and pipes it to md5sum / sha256sum. If the frozen hashes are wrong, this
# catches it; chirograph is not involved in the arithmetic.
#
# `jq` does the JSON extraction so that no Python is in the path at all. Falls
# back to a Python extractor if jq is unavailable, which weakens the
# independence slightly but still bypasses all chirograph code.
#
# Usage: tools/check_vectors_independently.sh
set -euo pipefail

VECTORS="$(dirname "$0")/../src/chirograph/vectors/canonicalisation.json"
[[ -f $VECTORS ]] || { echo "vectors not found at $VECTORS" >&2; exit 2; }

if command -v jq >/dev/null 2>&1; then
    extract() { jq -r '.canonicalisation[] | [.name, .hash_algorithm, .body_hash, .canonical_sha256, (.canonical|@base64)] | @tsv' "$VECTORS"; }
else
    echo "note: jq not found; using python for JSON extraction only (hashing still external)" >&2
    extract() {
        python3 - "$VECTORS" <<'PY'
import base64, json, sys
for case in json.load(open(sys.argv[1]))["canonicalisation"]:
    print("\t".join([
        case["name"], case["hash_algorithm"], case["body_hash"], case["canonical_sha256"],
        base64.b64encode(case["canonical"].encode("utf-8")).decode(),
    ]))
PY
    }
fi

pass=0; fail=0
while IFS=$'\t' read -r name algo expected_body expected_sha b64; do
    canonical_bytes=$(mktemp)
    printf '%s' "$b64" | base64 -d > "$canonical_bytes"

    case "$algo" in
        md5)    got_body=$(md5sum    < "$canonical_bytes" | cut -d' ' -f1) ;;
        sha256) got_body=$(sha256sum < "$canonical_bytes" | cut -d' ' -f1) ;;
        *)      echo "FAIL $name: unknown algorithm $algo"; fail=$((fail+1)); rm -f "$canonical_bytes"; continue ;;
    esac
    got_sha=$(sha256sum < "$canonical_bytes" | cut -d' ' -f1)
    rm -f "$canonical_bytes"

    if [[ $got_body == "$expected_body" && $got_sha == "$expected_sha" ]]; then
        pass=$((pass+1))
    else
        fail=$((fail+1))
        echo "FAIL $name"
        echo "     body_hash expected $expected_body got $got_body"
        echo "     canonical_sha256 expected $expected_sha got $got_sha"
    fi
done < <(extract)

echo "independent check: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
