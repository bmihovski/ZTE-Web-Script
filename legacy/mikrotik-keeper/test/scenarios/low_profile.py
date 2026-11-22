"""A single grouped SPV and one boot per changed ZTE low profile."""
import os
import sys
import tempfile

os.environ.update(DATA=tempfile.mkdtemp(), DEVICE="zte")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from keeper import acs, common, devices
from keeper.devices.zte import locks

modem = {"LockLTEBand": "1,3,8,20,48", "LockLTECell": "394,100", "LockNr5gCell": ""}
writes = []
def report():
    locks.on_values({locks.CI + key: value for key, value in modem.items()})

def accept(pairs, reboot=False):
    assert reboot
    writes.append({k.removeprefix(locks.CI): v for k, _, v in pairs})
    modem.update(writes[-1])
    acs.st["boots"] += 1
    report()
    return True

acs.tr069_set = accept
common.state({"lte_lock_fmt": {"lock": "{pci},{earfcn}", "unlock": ""}})
report()
locks.locks_apply("394@3774", "534@156510")
assert writes == [{"LockLTEBand": "8", "LockLTECell": "394,3774", "LockNr5gCell": "534,156510,28,15"}], writes
locks.locks_apply("394@3774", "534@156510")
assert len(writes) == 1
locks.locks_apply("394@6400")
assert modem["LockLTEBand"] == "20" and modem["LockLTECell"] == "394,6400"
locks.locks_apply("394@100", "none")
assert modem["LockLTEBand"] == "1,3,8,20,48" and modem["LockLTECell"] == "394,100"
locks.locks_apply("394@3774")
locks.locks_apply("none")
assert modem["LockLTEBand"] == "1,3,8,20,48" and modem["LockLTECell"] == "0,0"


import time
import threading
from keeper import absmoke, brain, guard
from keeper.devices.zte import profile

def fails(fn, reason):
    try:
        fn()
    except (RuntimeError, ValueError) as e:
        assert reason in str(e), e
    else:
        raise AssertionError("expected " + reason)

for cell, band in (("394@100", "8"), ("394@6400", "8"), ("394@7000", "20")):
    fails(lambda: locks.locks_apply(cell, band=band), "conflicts")

# Stop on a temporary low leg with raw locks which differ from keeper state.
modem.update(LockLTECell="340,100", LockNr5gCell="534,636576,78,30")
report()
original = modem.copy()
brain.wait_online = lambda **kw: True
absmoke._plan = lambda orig: [("A low", "394@3774", "534@156510")]
def stop_on_low(name, lte, nr, switch, stop):
    assert switch and common.state()["profile_restore"]["raw"] == original
    locks.locks_apply(lte, nr)
    assert modem["LockLTEBand"] == "8"
    stop.set()
    return {}
absmoke._leg = stop_on_low
absmoke.st.update(running="stop test", cancel=threading.Event())
absmoke._run(("none", "none"), False)
assert modem == original and not profile.pending()
assert absmoke.st["result"]["verdict"] == "stopped" and not absmoke.st["result"]["restore_error"]

# A kept low profile survives a temporary release, another low and a process restart.
locks.locks_apply("394@6400", "534@156510")
kept = modem.copy()
profile.begin_scope()
locks.locks_apply("none", "none")
locks.locks_apply("394@3774", "none")
profile.latest = None
devices.current().on_start()
report()
brain.wait_online = lambda **kw: False
fails(profile.finish_scope, "no fresh internet")
assert profile.pending() and modem == kept
count = len(writes)
brain.wait_online = lambda **kw: True
profile.finish_scope()
assert not profile.pending() and len(writes) == count
assert common.state()["lte_prior_band"] == original["LockLTEBand"]

# An offline LTE release uses the web mask, reads back staged fields and leaves NR alone.
forms = []
class Page:
    def login(self): return None
    def logout(self): pass
    def post(self, form):
        forms.append(form)
        if form["goformId"] == "BAND_SELECT":
            assert form["lte_band_mask"] == "0x%011x" % profile.band_mask(original["LockLTEBand"])
            modem["LockLTEBand"] = original["LockLTEBand"]
        elif form["goformId"] == "LTE_LOCK_CELL_SET":
            modem["LockLTECell"] = "0,0"
        elif form["goformId"] == "REBOOT_DEVICE":
            acs.st["boots"] += 1
            report()
        return {"result": "success"}
    def get(self, cmd):
        return {"lte_band_lock": hex(profile.band_mask(modem["LockLTEBand"])), "lte_pci_lock": "0", "lte_earfcn_lock": "0"}
profile.Web = Page
common.state({"lte_lock": {"cell": "394@6400", "state": "active"}})
brain.no_internet = lambda now: True
assert guard.offline_release(9999)
assert common.state()["lte_lock"]["state"] == "released"
assert [f["goformId"] for f in forms] == ["BAND_SELECT", "LTE_LOCK_CELL_SET", "REBOOT_DEVICE"]
assert modem["LockNr5gCell"] == kept["LockNr5gCell"] and not common.state()["lte_prior_band"]

# Missing/stale reads block capture. An acknowledgement without matching post-BOOT values
# leaves rollback durable. A web rejection after staging the band never triggers a reboot.
profile.BOOT_S = 0.01
modem.update(original)
report()
good = profile.latest
profile.latest = (good[0] - 100, *good[1:])
fails(lambda: locks.locks_apply("394@3774"), "fresh")
report()
del modem["LockLTEBand"]
report()
fails(lambda: locks.locks_apply("394@3774"), "invalid")
modem.update(original)
report()
for failure in ("no BOOT", "partial", "refused"):
    def fail_write(pairs, reboot=False):
        if failure == "refused": return False
        modem["LockLTEBand"] = "8"
        if failure == "partial": acs.st["boots"] += 1
        report()
        return True
    class Reject(Page):
        def post(self, form):
            assert form["goformId"] != "REBOOT_DEVICE"
            if form["goformId"] == "BAND_SELECT":
                modem["LockLTEBand"] = "8"
                return {"result": "success"}
            return {"result": "failure"}
    profile.Web = Reject
    acs.tr069_set = fail_write
    fails(lambda: locks.locks_apply("394@3774", "534@156510"), {"refused": "refused", "partial": "requested", "no BOOT": "no BOOT inform"}[failure])
    assert profile.pending()
    acs.tr069_set = accept
    profile.finish_scope()
    assert modem == original and not profile.pending()

# Radio proof needs the exact config and serving pair in each transfer.
target = dict(LockLTECell="394,3774", LockNr5gCell="534,156510,28,15", LockLTEBand="8")
row = dict(target, boots=acs.st["boots"], lte_cell="3774/394", nr_cell="156510/534", lte_ca="B8/3774/394:-90/4@5")
def proof():
    return profile.transfer_evidence([(10,20),(30,40)], target, "394@3774", "534@156510")["verified"]
profile.radio.clear()
profile.radio.append(dict(row, time=12))
assert not proof()
profile.radio.append(dict(row, time=32))
assert proof()
for change in ({"LockLTEBand":"20"}, {"lte_cell":"6400/394"}, {"nr_cell":"636576/534"}, {"lte_cell":""}):
    profile.radio.append(dict(row, time=33, **change))
    assert not proof()
    profile.radio.pop()
print("OK test_low_profile")
