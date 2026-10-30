"""Device drivers: everything that depends on the modem model lives here; the rest of the keeper is device neutral.

One driver per DEVICE value (`zte`, `mikrotik`), selected once at start by `current()`. A driver is a module with:

| name | what |
|---|---|
| `NAME`, `MAKER` | display name; the TR-069 Inform `Manufacturer` it answers to (another CPE pointed here is ignored) |
| `CAN` | features it supports: `antennas`, `sweep`, `lock_test`, `night`, `checkin`, `scan`, `at` - the API refuses the rest (409) |
| `READ` | TR-069 parameter paths read every inform |
| `EXTRA` | sample fields only this device has (`zte_*` / `mikrotik_*` prefix, or a shared name the other can't fill) |
| `row(vals)` | one read -> one sample dict with the shared fields (`nr_cell`, `lte_cell`, `lte_ca`, ... see `keeper.monitor.SAMPLE_COLS`) |
| `minute_jobs()` | functions called with each sample, in order (lock guard, night screen, sweep, scan ...) |
| `locks_apply(lte, nr)` | set / remove cell locks (`'pci@channel'`, `'none'`, None = leave), blocking until applied |
| `lock_lte(spec)` | LTE lock + reboot for a lock test (`None` = unlock) |
| `lock_value(key)` | (label, raw value) of the lock the modem itself holds for `lte_lock` / `nr_lock`, None = unknown - the guard reconciles with it |
| `nr_on(row, cell)` | is the sample served by the locked 5G cell |
| `on_start()`, `on_boot()` | hooks: keeper start; a BOOT inform from the modem |
| `on_values(vals)` | hook after each read: returns extra (name, type, value) to set in the same session |
| `pending_rpc(host)` | an ACS request to send before a reboot (MikroTik: a script Download), or None |
| `on_rpc(kind, msg, step)` | hook for DownloadResponse / TransferComplete / Fault |
| `start_note()` | device part of the start log line |
| `relay(handler, body)` | operator check-in relay: a reply tuple when this session goes to the operator, else None |
| `config()` | device part of `/status` -> `config` |

Platform helpers stay inside the driver (`zte.web`, `zte.antennas`, `zte.checkin`; `mikrotik` script queue).
"""
import importlib

NAMES = ("zte", "mikrotik")
_dev = {}


def current():
    """The driver module of DEVICE (loaded once). Raises KeyError for an unknown DEVICE."""
    from keeper import store
    if store.DEVICE not in NAMES: raise KeyError("DEVICE=%s unknown (%s)" % (store.DEVICE, " | ".join(NAMES)))
    if "m" not in _dev: _dev["m"] = importlib.import_module("keeper.devices." + store.DEVICE)
    return _dev["m"]
