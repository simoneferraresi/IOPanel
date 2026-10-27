# Contributing to IOPanel

Thank you for helping improve IOPanel. This is scientific software used with
laboratory instruments, so changes should remain reviewable and should not
silently alter device behavior.

## Before contributing

- Search [existing issues](https://github.com/simoneferraresi/IOPanel/issues)
  and open an issue for significant changes, particularly hardware-related work.
- Use a focused branch and pull request for one issue or cohesive improvement.
  Explain the motivation, scope, validation evidence, and known limitations.
- Avoid committing local secrets, instrument serial numbers, machine-specific
  credentials, measurements, or generated runtime files.
- Do not assume that a simulator or CI result qualifies real devices.

## Driver-free development

Use Python 3.12 and the pinned uv environment:

~~~powershell
uv sync --locked --no-default-groups --extra test --extra typecheck --extra lint
uv run --no-default-groups --no-sync pytest -q
uv run --no-default-groups --no-sync ruff check .
uv run --no-default-groups --no-sync ruff format --check .
uv run --no-default-groups --no-sync pyright
~~~

Run appropriate focused tests during implementation, and run
`git diff --check` before proposing a PR. The GitHub Actions workflow tests
the hardware-independent suite; any real-device acceptance must be performed
separately and recorded clearly.

## Laboratory hardware and safety

Do not run camera discovery, CT400 scans, piezo motion, or other physical-device
operations as an incidental side effect of tests, scripts, or documentation work.
Physical-device testing must take place under operator supervision on a lab PC
with its compatible drivers, vendor software, and connected instruments.
See the [hardware validation protocol](docs/laboratory-hardware-validation.md)
and the [compatibility profiles](docs/vmbpy-compatibility.md).

## Documentation contributions

Keep the [root README](README.md) concise and oriented toward first-time
visitors. Place step-by-step technical material in the
[technical guide](docs/technical-guide.md) or in dedicated documents, and
link it from the [documentation index](docs/README.md).
Use real, versioned screenshots, meaningful alt text, and accurate badge
destinations. Do not add release/download claims before an actual published,
supported distribution exists.

## Review and merge

Describe the checks you performed and distinguish automated, simulated, and
physical acceptance. Open a draft PR if the changes are not ready for use.
Maintainers decide when reviews and qualification are sufficient for merging.
