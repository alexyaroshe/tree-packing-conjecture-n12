# Retained certificate samples

5,604,352 packings from the original n=12 run: 1024 certificates from each of 5,473 sampled batches. Complete streams were discarded after verification. The full ledger is at [10.5281/zenodo.23537606](https://doi.org/10.5281/zenodo.23537606)

| File | Contents |
|---|---|
| [samples.cert.gz](samples.cert.gz) | Certificate samples |
| [index.tsv](index.tsv) | Batch ranges, offsets & hashes |

Each certificate is 66 bytes; see the [format](../README.md). The index gives each batch’s decompressed byte offset, sequence start, certificate count and SHA256. Check one indexed range at a time

## Check the first sample

After [building](../README.md), run from the repository root:

```sh
sample_directory=$(mktemp -d)
gzip -dc certificates/samples.cert.gz > "$sample_directory/all.cert"
dd if="$sample_directory/all.cert" of="$sample_directory/first.cert" bs=66 count=1024 status=none
for checker in check_a check_b; do
  "../n12-build/$checker" --n 12 --trees trees.txt --cert "$sample_directory/first.cert" --res 0 --mod 1 --limit 1024
done
```

For other samples, use byte_offset, start and count from index.tsv.

## Origin

```text
Campaign: c0a2ff24dd86f8a43445db4009026deebc2001631e6be741bb90301dc22daf53
Ledger SHA-256: ee01149d78657f8ffc9a4feec0ffdfda6d60395a0ad0f1b21c96948d00e14264
```
