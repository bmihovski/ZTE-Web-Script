# A/B candidate score dict (2026-11-21): the pair rule - top score's cells if >1, else the top + the next score's first; a single
# cell -> no B; the low pair from the band scans by call-ready, ties keep both.
import os, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA)
sys.path.insert(0, ROOT)
from keeper import abplan, brain, store
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "score": 90}, {"lock": "340@3350", "score": 80},
                                           {"lock": "370@1550", "score": 70}]}
assert abplan.high() == ["434@1550", "340@3350"], abplan.high()          # the max under A, the next under B
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "score": 90}, {"lock": "340@3350", "score": 90}]}
assert abplan.high() == ["434@1550", "340@3350"], abplan.high()          # the same top score -> both
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "score": 90}]}
assert abplan.high() == ["434@1550"], abplan.high()                      # a single cell -> no B
store.add(DATA, "band_scans", {"band": "8", "serving": "3774/394", "nr_cell": "156510/534", "call_ok": "100.0"})
store.add(DATA, "band_scans", {"band": "20", "serving": "6400/112", "nr_cell": "156510/534", "call_ok": "100.0"})
assert abplan.low() == [("394@3774", "534@156510"), ("112@6400", "534@156510")], abplan.low()   # the tie -> both
print("OK abplan")
