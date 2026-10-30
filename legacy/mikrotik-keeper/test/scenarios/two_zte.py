# a second ZTE (the MC888) pointed at the MC889's keeper must be ignored, not mixed into its samples / clock.
# The old guard filtered by Manufacturer only (built for the MikroTik ATL); two ZTEs looked identical.
import os, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), CHECKIN_DAYS="0", LOCK_PAUSE="", NIGHT_SCREEN="off")
sys.path.insert(0, ROOT)
from keeper import acs, common

inform = lambda prod: ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">'
    '<s:Header><cwmp:ID>1</cwmp:ID></s:Header><s:Body><cwmp:Inform>'
    '<DeviceId><Manufacturer>ZTE</Manufacturer><ProductClass>%s</ProductClass><SerialNumber>SN-%s</SerialNumber></DeviceId>'
    '<Event><EventStruct><EventCode>2 PERIODIC</EventCode></EventStruct></Event><CurrentTime></CurrentTime>'
    '<ParameterList/></cwmp:Inform></s:Body></s:Envelope>') % (prod, prod)

class H:                                    # the bits acs.serve touches on the HTTP handler
    def __init__(self): self.headers = {"Host": "192.168.6.250:7557"}

common.clock["ok"] = True                   # pretend the clock was already verified

# first ZTE seen = the managed one (MC889): handled, adopted, and its empty CurrentTime leaves the clock alone
h = H(); s, hd, body = acs.serve(h, inform("MC889"))
assert not getattr(h, "foreign", False) and b"InformResponse" in body, (getattr(h, "foreign", None), body[:90])
assert common.state().get("cpe") == "MC889", common.state().get("cpe")
assert common.clock["ok"] is True, common.clock                      # empty CurrentTime must NOT flip the verdict

# a second ZTE model (MC888) pointed here: foreign -> answered but left alone, clock untouched, nothing adopted over
h = H(); s, hd, body = acs.serve(h, inform("MC888"))
assert h.foreign and b"InformResponse" in body, (getattr(h, "foreign", None), body[:90])
assert common.clock["ok"] is True and common.state().get("cpe") == "MC889", (common.clock, common.state().get("cpe"))

# an explicit CPE= env pins regardless of first-seen
os.environ["CPE"] = "MC888"
h = H(); acs.serve(h, inform("MC889")); assert h.foreign, "MC889 should be foreign when CPE=MC888"
h = H(); acs.serve(h, inform("MC888")); assert not h.foreign, "MC888 should be managed when CPE=MC888"
print("OK two_zte")
