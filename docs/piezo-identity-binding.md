# Piezo USB identity binding (experimental, operator-supervised)

## Why this exists

Windows assigns COM-port names dynamically. COM3 today may be COM7 tomorrow; treating
COM numbers as stable stage identities risks addressing the wrong stage. Two physical
Thorlabs MDT693B interfaces on the laboratory computer were enumerated as COM3 and COM4,
while COM5 was a Prolific serial adapter. None of those COM labels proves Left/Right
wiring, and an adapter's USB serial is not the Thorlabs controller's native ID.

## Safe discovery and connection

**Startup:** With at least one configured role, a worker enumerates Windows serial
metadata using PySerial. It does not call the vendor's `List` or `Open` on
unselected ports, and does not connect either piezo automatically. Creating
disconnected wrappers may load the configured vendor DLL. The Instruments menu
shows separate Left/Right connection indicators.

**Explicit Connect:** The operator's selected role resolves to one current COM
port. Only then does the driver initialize the vendor DLL with `List` and
`Open` that one resolved identifier. No voltage or stage movement is commanded
by discovery or this explicit connection workflow.

**Never guess:** For an explicitly bound USB serial, zero or multiple matching
serial ports mean the role is unavailable, **not** a fallback to the old COM
value. Both roles resolving to one port disables both. Legacy COM matching
remains available when no stable binding was configured.

## Configure using the GUI

Open **Instruments > Configure Piezo Controllers…**. The table enumerates ports,
Windows descriptions, USB-interface serials, VID:PID and manufacturers. It does
not open a COM port or query the vendor DLL. Check physical wiring and labels
with an operator before picking Left and Right. Duplicate/missing USB serials
are not eligible as stable bindings. The acknowledgement checkbox enables
**Copy config.ini bindings**; this action only copies the following two lines:

```ini
piezo_left_usb_serial = <verified unique USB-interface serial>
piezo_right_usb_serial = <verified unique USB-interface serial>
```

Paste the values into the existing `[Instruments]` section of the local
`config.ini`, after making an appropriate local backup, then restart.
The dialog **does not** change the config file itself, because hardware
configuration is deliberately distinct from per-user visual preferences.
Leaving a value empty retains the existing configured COM for that side.

When a USB serial is unavailable (common for some serial interfaces), the
current release cannot safely bind that controller by serial and retains the
legacy COM mapping. Do not reuse a shared adapter serial to represent two
physical controllers. A future **native Thorlabs ID** integration would require
verifying what the vendor `List` and `GetId` calls return for each installed
controller, including whether these IDs are unique and portable.

## Physical qualification and restrictions

Before any motion, verify the physical Left/Right assignments, then test each
normal GUI Connect/Disconnect, confirm the CT400 is not yet explicitly
connected, and only then carry out the operator-supervised #144 UI interlock
matrix. The safe test does **not** click Fine Tune, Spiral Alignment, or
Generate 3D Map. CT400 Connect may enable a laser input; follow the lab's
approved optical procedure.

CI exercises identity matching and UI with fakes and does not qualify actual
serial drivers, DLL firmware, vendor GetId, optical safe state or motion.
