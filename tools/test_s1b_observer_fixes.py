import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from s1b_system_r1 import tcl_observer

W = Path(__file__).resolve().parents[1]


class ObserverFixTests(unittest.TestCase):
    def test_internal_width_preserves_every_core_and_cluster_element(self):
        sv = (W / "tools/s1b_readonly_observer_r4.sv").read_text()
        self.assertIn(".DW(WdTaskDescWidth)", sv)
        self.assertNotIn(".DW(TaskDescWidth)", sv)
        self.assertIn("DW == 62", sv)
        # Independent packed-array oracle: unique IDs and nonzero class at MSB
        # catch stride truncation across all six elements, not just element 0.
        words = [(i << 59) | ((17+31*i) << 9) | (i % 2 << 7) for i in range(6)]
        packed = sum(word << (62*i) for i, word in enumerate(words))
        ids = [(packed >> (62*i+9)) & 0xfff for i in range(6)]
        self.assertEqual(ids, [17+31*i for i in range(6)])
        bad = [(packed >> (59*i+9)) & 0xfff for i in range(6)]
        self.assertNotEqual(bad, ids)
        self.assertIn("checkout_desc[c][cl][IDW+8:9]", sv)
        self.assertIn("waiting_desc[c][IDW+17]", sv)

    def run_tcl(self, body):
        sealed = json.loads((W / "evidence/PredictionsS1bR4/predictions.json").read_text())
        generated = tcl_observer(sealed, True)
        self.assertNotIn("[now]", generated)
        stubs = r'''
set callbacks [dict create]
proc when {args} {global callbacks; dict set callbacks [lindex $args 1] [lindex $args 3]}
proc nowhen {label} {global callbacks; dict unset callbacks $label}
proc echo {text} {puts $text}
proc examine {args} {return 5}
proc force {args} {}
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["tclsh"], input=stubs+generated+body, text=True,
                                    cwd=directory, capture_output=True, timeout=10)
            clocks = Path(directory, "clock_observations.txt").read_text()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout, clocks

    def test_tcl_callbacks_preserve_integer_ps_over_32_bits(self):
        stdout, clocks = self.run_tcl(r'''
foreach stamp {13381710000 13381738000 13381766000 13381794000} {
    set ::now $stamp
    eval [dict get $callbacks S1B_CLOCK]
}
set ::now 13381794000
eval [dict get $callbacks S1B_COMPLETION]
eval [dict get $callbacks S1B_INVALID]
close $S1B_CLOCK_FILE
''')
        self.assertEqual(list(map(int, clocks.splitlines())),
                         [13381710000, 13381738000, 13381766000, 13381794000])
        self.assertIn("S1B_COMPLETION_WRITTEN time=13381794000 word=5", stdout)
        self.assertIn("S1B_INVALID_TRIGGER time=13381794000", stdout)
        self.assertIn("S1B_INVALID_WRITTEN time=13381794000", stdout)

    def test_empty_or_unit_ambiguous_tcl_time_is_unobservable(self):
        stdout, _ = self.run_tcl(r'''
foreach stamp {"" "13 ns" -1} {
    set ::now $stamp
    if {![catch {s1b_time_ps}]} {error "invalid timestamp accepted"}
}
unset ::now
if {![catch {s1b_time_ps}]} {error "missing timestamp accepted"}
puts "INVALID_TIME_STOP_PASS"
close $S1B_CLOCK_FILE
''')
        self.assertIn("INVALID_TIME_STOP_PASS", stdout)


if __name__ == "__main__":
    unittest.main()
