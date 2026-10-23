# F04 — Buffered schema-v1/v2 archival CSV writing

The production writer in `logic/export_file_io.py` already sends `numpy.savetxt` directly into a staged local file. Both scan and Power Monitor exports preserve their respective metadata schemas, `%.17g` archival float64 precision, flush/fsync durability and atomic publish/overwrite guarantees.

The new version removes `write_through=True` from the text wrapper. NumPy writes CSV one row at a time; ordinary text buffering coalesces those writes. Explicit `stream.flush()` and `os.fsync(stream.buffer.fileno())` remain in place **before** atomic publication.

## Same-process Windows A/B

Run: [CI #39939123151](https://github.com/simoneferraresi/IOPanel/actions/runs/39939123151). [Original JSON evidence](https://github.com/simoneferraresi/IOPanel/actions/runs/39939123151/artifacts/12495655201).

- Windows Server 2025, CPython 3.12.10, NumPy 2.5.3, synthetic 100,000 × 5 columns with NaN/Inf and non-ASCII metadata, no physical instruments.
- Four alternating-order trials per mode after one warmup per mode. All eight trial output files were SHA-256 identical.
- `write_through=True` timings: 0.3903, 0.3788, 0.3887, 0.3817 seconds; median **0.3852 s**.
- Ordinary text-buffered timings: 0.3577, 0.4202, 0.3702, 0.3393 seconds; median **0.3640 s**.
- **Median difference −5.5%**. Three of four buffered trials were faster, one was slower. This small benefit is noisy; it must not be presented as a guaranteed or large speedup.

These experiments use the same NumPy text formatting, staged file creation, flush and fsync, but not the entire application/GUI or network/filesystem publication latency. Tests additionally verify exact archival bytes, IEEE-754 roundtrips, output staging and cleanup for both export domains.

To repeat without vendor drivers, in the locked environment:

```powershell
uv run python -m tools.benchmark_csv_write_buffering --points 100000 --repetitions 4 --output f04-buffering.json
```

The temporary CI benchmark job was removed after capturing the evidence, keeping the normal Windows checks and packaging unchanged.
