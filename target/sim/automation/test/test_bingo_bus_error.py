"""Bus errors of the bingo manager's AXI-Lite masters (bingo_hw_manager_top
bus_error_o): read-only in BINGO_STATUS[11:9], and a problem in every scene
the generic checker judges."""
import importlib.util
from pathlib import Path
import re
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_bus_error", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


class BusErrorTests(unittest.TestCase):
    def test_status_field_after_the_existing_ones(self):
        text = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph_reg.hjson.tpl").read_text()
        start = text.index('name: "BINGO_STATUS"')
        block = text[start:text.index("\n      ]", start)]
        fields = re.findall(r'bits: "([0-9:]+)", resval: "0", name: "(\w+)"', block)
        self.assertEqual(fields, [("0", "REPLAY_STUCK"), ("1", "REMOTE_DONE_MISMATCH"),
                                  ("7:2", "REMOTE_LINK_ERROR"), ("8", "REMOTE_TIMEOUT"),
                                  ("11:9", "BUS_ERROR")])

    def test_wired_from_the_manager_to_the_register(self):
        periph = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph.sv").read_text()
        self.assertIn("input  logic [2:0]  bingo_hw_manager_bus_error_i,", periph)
        self.assertIn("assign hw2reg.bingo_status.bus_error.d             = bingo_hw_manager_bus_error_i;", periph)
        self.assertIn("assign hw2reg.bingo_status.bus_error.de            = 1'b1;", periph)
        ctrl = (ROOT / "hw/occamy/occamy_quad_ctrl.sv.tpl").read_text()
        self.assertIn(".bingo_hw_manager_bus_error_i             (bingo_bus_error                     ),", ctrl)
        self.assertIn(".bus_error_o                               (bingo_bus_error                      )", ctrl)

    def test_a_bus_error_line_is_a_problem(self):
        scene = watchdog.SCENARIOS["s1"]
        log = "All chips finished successfully at 90\n"
        uart = "Check [C]: PASS\n"
        without = watchdog.evaluate("s1", scene, log, uart)
        line = "[BINGO_BUS_ERROR] 1000 PM write answered with resp 2"
        with_error = watchdog.evaluate("s1", scene, line + "\n" + log, uart)
        extra = [p for p in with_error if p not in without]
        self.assertEqual(len(extra), 1, extra)
        self.assertIn("1 [BINGO_BUS_ERROR] event(s)", extra[0])
        self.assertIn(line, extra[0])
        self.assertFalse(any("BUS_ERROR" in p for p in without))


if __name__ == "__main__":
    unittest.main()
