# Windows x64 one-folder distribution

The package targets 64-bit Windows and CPython 3.12. It is built from the locked core, `dev`, and `test` dependency sets; the optional `camera` and `matlab` extras are not installed. The PyInstaller spec is `packaging/IOPanel.spec`, and output is the unpacked `dist/IOPanel/` directory.

## Reproduce the build

From a clean Windows x64 checkout at the repository root, install the pinned uv release and locked dependencies, then run:

```powershell
uv sync --locked --extra dev --extra test
uv run --no-sync pyinstaller --clean --noconfirm packaging/IOPanel.spec
Copy-Item packaging/config.simulation.ini dist/IOPanel/config.ini
uv run --no-sync python tools/smoke_frozen.py dist/IOPanel/IOPanel.exe
```

Distribute the entire `dist/IOPanel/` folder together and launch `IOPanel.exe` from any working directory. The external `config.ini` beside the EXE is a driver-free example profile. Edit a copy for site use; never substitute the repository's lab-specific `config.ini` into a general distribution. The CI smoke launches this actual executable from a different CWD, verifies Qt resources and simulated device startup, and requires logged graceful shutdown before its 45-second timeout.

## Configuration and writable paths

In a frozen build, the default config is `config.ini` beside `IOPanel.exe`; an absolute `--config` path takes precedence. In source mode, config paths remain relative to the process working directory. Frozen relative log paths (from the config or `--log-file`) resolve under `%LOCALAPPDATA%\IOPanel`; if that location cannot be created, the application uses the system temporary directory. Absolute `--log-file` paths are preserved. Source mode keeps existing current-working-directory log behavior.

Frozen export dialogs default to `%LOCALAPPDATA%\IOPanel\output` (or the system temporary directory if that cannot be created); source mode keeps its current-working-directory default. Users can select another writable folder in each dialog. The executable does not write into PyInstaller's `_internal` directory.

## Profiles and external integrations

`packaging/config.simulation.ini` explicitly selects the simulated CT400 and one simulated camera. `--smoke-test` refuses a physical CT400 backend or any enabled non-simulated camera before constructing the main window. It suppresses MATLAB prewarming and closes through the normal Qt window lifecycle. CI does not install hardware drivers or SDKs.

Physical deployment remains external to this package profile. CT400 DLL paths in a separately approved site INI remain external and are not bundled. Vimba X and its camera SDK must be installed and configured on the target host before selecting the Vimba backend. MATLAB Engine and MATLAB itself are not bundled; MATLAB support requires a compatible separately installed MATLAB/Engine setup and the optional `matlab` extra in a separately qualified environment. This package does not qualify physical hardware or cross-host SDK compatibility.

## Build scope

PyInstaller's analysis and platform hooks package the locked application's imported Python and Qt runtime dependencies, including the already compiled Qt resources. The spec explicitly excludes MATLAB, MATLAB Engine, and VmbPy. The output is an unpacked one-folder application with a visible console for startup diagnostics. No installer, updater, signing, or proprietary SDK redistribution is included.
