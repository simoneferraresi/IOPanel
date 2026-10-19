# Standalone camera and piezo discovery utilities

## Status and scope

**Retained intentionally.** The [October 2026 legacy audit](test-legacy-audit-2026-10-18.md)
found no in-repository callers for `cameras_info.py` or `find_piezos.py`.
That **does not establish that operators never invoke them manually**. Both
commands remain at their original paths, and running either with no arguments
retains its inventory function. Do not delete, rename or silently replace these
entry points without checking lab practice and validating a replacement.

These are **opt-in, source-checkout, hardware-facing diagnostic commands**, not
parts of the packaged executable or of the ordinary application startup.
Importing the Python modules and using `--help` are hardware independent.
**Running discovery is not hardware-independent**, even though the scripts do
not intentionally initiate image streaming, piezo motion, laser control or a
scan. Vendor SDKs may perform internal initialization and I/O.

| Tool | Discovery operation | Prerequisites | What it does *not* qualify |
| --- | --- | --- | --- |
| `cameras_info.py` | Enters `VmbSystem`, calls `get_all_cameras()` and prints vendor descriptions while the context is active | Installed, working VmbPy and Vimba X/transport | Camera opening, frames, SDK/firmware compatibility or streaming |
| `find_piezos.py` | Loads an explicitly identified MDT vendor DLL and calls its `List` entry point, parsing COM and long numeric identifiers | Matching DLL/driver/bitness, connected Thorlabs MDT hardware | Successful port opening, controller identity/side, movement or electrical safe state |

The application's separate `PiezoController.find_devices()` goes **further**
than this inventory script: it probes system COM ports using the vendor
`Open` / `Close` calls. Neither route is a substitute for an authorized
physical validation.

## Recommended workflow

Start with **static** checks, which never load the camera SDK or instrument DLL:

```powershell
python -m tools.check_environment --config config.ini
python cameras_info.py --help
python find_piezos.py --help
```

Only on an authorized lab PC, with IOPanel and conflicting vendor acquisition
applications closed, an operator may then request discovery:

```powershell
python cameras_info.py
python find_piezos.py --dll "C:\Program Files (x86)\Thorlabs\MDT69XB\Sample\Thorlabs_MDT69XB_PythonSDK\MDT_COMMAND_LIB_x64.dll"
```

The piezo command still defaults to the original lab DLL location when `--dll`
is omitted; specifying `--dll` is recommended for portability. The parser
retains the previous COM-port and long-numeric-serial recognition rules. If
the DLL is missing, cannot be loaded, its `List` call fails, or its output
cannot be parsed, the command returns a nonzero status rather than guessing
an identifier. No configuration file is modified.

VmbPy is optional in driver-free developer/CI environments, so
`cameras_info.py` imports it only when discovery is explicitly invoked.
A missing or broken SDK produces an error; the script does **not** silently
fall back to simulated cameras. The camera inventory prints raw vendor object
descriptions, not independently verified device assignments.

The piezo identifiers are **candidates** for the existing
`piezo_left_serial` and `piezo_right_serial` fields in `config.ini`.
Do not infer left/right from enumeration order. Record and independently
verify the physical controller assignment before any alignment or motion.

## Testing and acceptance

`tests/test_standalone_discovery_cli.py` covers SDK-free imports, `--help`,
VmbPy inventory with an injected fake system, missing SDK handling, DLL
presence, parsing, negative vendor results and the **List-only** call boundary.
The tests never load the real vendor libraries or connect to a device.

For real hardware, perform separate supervised validation using the
[laboratory hardware protocol](laboratory-hardware-validation.md); CI success
is not physical qualification. Do not run either inventory during a live
experiment or while another process owns the same instrument. Keep serial
numbers and lab installation paths out of public logs and issues where
possible.

## Future deprecation decision

Before retiring either command, confirm with laboratory operators whether it
remains in runbooks, desktop shortcuts, troubleshooting instructions or setup
procedures. If a maintained replacement offers equivalent raw device inventory,
prove its side-effect and output behavior on the lab PC, then document an
operator-approved transition. Until then **both entry points stay supported**.
