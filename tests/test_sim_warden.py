# MIT License
#
# Copyright (c) 2024 <COPYRIGHT_HOLDERS>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

"""Isaac-free unit tests for ``SimWarden`` config parsing and dispatch logic.

The module is loaded directly from its file so importing it does not pull in the extension's
``omni.*`` dependencies; these tests run under plain ``python -m unittest`` in CI without Isaac Sim.
"""

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_SIM_WARDEN_PATH = Path(__file__).resolve().parents[1] / "simyard" / "scenario" / "configurator" / "sim_warden.py"
_spec = importlib.util.spec_from_file_location("simyard_sim_warden_under_test", _SIM_WARDEN_PATH)
sim_warden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim_warden)

SimWarden = sim_warden.SimWarden


def _write(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class _FakeSettings:
    """Minimal stand-in for a carb settings interface. Unknown keys read back as None."""

    def __init__(self, known=None):
        self._known = dict(known or {})
        self.sets = {}

    def get(self, key):
        return self._known.get(key)

    def set(self, key, value):
        self.sets[key] = value
        self._known[key] = value


class _RecordingCarb:
    """Fake ``carb`` module that records logged warnings and hands out fake settings."""

    def __init__(self, settings=None):
        self._settings = settings if settings is not None else _FakeSettings()
        self.warnings = []
        self.settings = types.SimpleNamespace(get_settings=lambda: self._settings)

    def log_warn(self, message):
        self.warnings.append(message)

    def log_info(self, message):
        pass

    def log_error(self, message):
        pass


class TestFromFile(unittest.TestCase):
    def test_parses_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d), "c.json", json.dumps({"physics": {"gravity": -9.81}}))
            warden = SimWarden.from_file(path)
            self.assertEqual(warden._config, {"physics": {"gravity": -9.81}})

    def test_parses_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d), "c.yaml", "physics:\n  gravity: -9.81\n  solver_type: TGS\n")
            warden = SimWarden.from_file(path)
            self.assertEqual(warden._config, {"physics": {"gravity": -9.81, "solver_type": "TGS"}})

    def test_yaml_and_json_equivalent(self):
        cfg = {"physics": {"gravity": -9.81, "physx.TimeStepsPerSecond": 70}, "rendering": {"/rtx/rendermode": "X"}}
        with tempfile.TemporaryDirectory() as d:
            jp = _write(Path(d), "c.json", json.dumps(cfg))
            yp = _write(
                Path(d),
                "c.yaml",
                'physics:\n  gravity: -9.81\n  "physx.TimeStepsPerSecond": 70\nrendering:\n  "/rtx/rendermode": "X"\n',
            )
            self.assertEqual(SimWarden.from_file(jp)._config, SimWarden.from_file(yp)._config)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            SimWarden.from_file("/no/such/config.yaml")

    def test_unsupported_extension_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d), "c.txt", "physics: {}")
            with self.assertRaises(ValueError):
                SimWarden.from_file(path)

    def test_non_mapping_root_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d), "c.json", json.dumps([1, 2, 3]))
            with self.assertRaises(ValueError):
                SimWarden.from_file(path)

    def test_empty_document_is_empty_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d), "c.yaml", "")
            self.assertEqual(SimWarden.from_file(path)._config, {})


class TestSection(unittest.TestCase):
    def test_physics_section(self):
        warden = SimWarden({"physics": {"gravity": -1.0}, "other": {"x": 1}})
        self.assertEqual(warden._section(("physics",)), {"gravity": -1.0})

    def test_rendering_alias_global_settings(self):
        warden = SimWarden({"global_settings": {"/rtx/rendermode": "X"}})
        self.assertEqual(warden._section(("rendering", "global_settings")), {"/rtx/rendermode": "X"})

    def test_missing_section_is_empty(self):
        self.assertEqual(SimWarden({})._section(("physics",)), {})


class TestSplitPhysics(unittest.TestCase):
    def test_splits_by_prefix(self):
        context, usd, physx = SimWarden._split_physics(
            {
                "gravity": -9.81,
                "context.physics_dt": 0.016,
                "usd.GravityMagnitude": 1.62,
                "physx.TimeStepsPerSecond": 70,
            }
        )
        self.assertEqual(context, {"gravity": -9.81, "physics_dt": 0.016})
        self.assertEqual(usd, {"GravityMagnitude": 1.62})
        self.assertEqual(physx, {"TimeStepsPerSecond": 70})


class TestConvertValue(unittest.TestCase):
    def test_string_booleans(self):
        self.assertIs(sim_warden._convert_value("true"), True)
        self.assertIs(sim_warden._convert_value("false"), False)

    def test_passthrough(self):
        self.assertEqual(sim_warden._convert_value(70), 70)
        self.assertEqual(sim_warden._convert_value("MBP"), "MBP")


class TestApplyReport(unittest.TestCase):
    def test_apply_returns_report_without_isaac(self):
        # Outside Isaac (no carb / no pxr) apply() must not raise and must return the report shape.
        report = SimWarden({"physics": {"gravity": -9.81}, "rendering": {"/rtx/rendermode": "X"}}).apply()
        self.assertIn("unknown_rendering_keys", report)
        self.assertIn("unhandled_physics_keys", report)
        self.assertIsInstance(report["unknown_rendering_keys"], list)
        self.assertIsInstance(report["unhandled_physics_keys"], list)


class TestUnknownKeyReporting(unittest.TestCase):
    """A non-existent parameter must be reported AND logged as a warning."""

    def test_rendering_unknown_key_is_flagged_and_logged(self):
        settings = _FakeSettings(known={"/rtx/rendermode": "X"})
        carb = _RecordingCarb(settings)
        warden = SimWarden({"rendering": {"/rtx/rendermode": "Y", "/rtx/does/not/exist": True}})
        with mock.patch.object(sim_warden, "carb", carb):
            unknown = warden.apply_rendering()
        self.assertEqual(unknown, ["/rtx/does/not/exist"])
        self.assertEqual(settings.sets.get("/rtx/rendermode"), "Y")  # known key still applied
        self.assertTrue(any("/rtx/does/not/exist" in w for w in carb.warnings))

    def test_dispatch_unknown_key_is_reported_and_logged(self):
        class Target:
            def set_gravity(self, value):
                self.gravity = value

        carb = _RecordingCarb()
        with mock.patch.object(sim_warden, "carb", carb):
            unhandled = SimWarden._dispatch_methods(Target(), {"gravity": -9.81, "bogus_param": 1})
        self.assertEqual(unhandled, ["bogus_param"])
        self.assertTrue(any("bogus_param" in w for w in carb.warnings))

    def test_schema_attr_missing_returns_false_and_logs(self):
        class Schema:  # no Get<Name>Attr / Create<Name>Attr
            pass

        carb = _RecordingCarb()
        with mock.patch.object(sim_warden, "carb", carb):
            ok = SimWarden._set_schema_attr(Schema(), "NoSuchAttr", 1, "PhysX")
        self.assertFalse(ok)
        self.assertTrue(any("NoSuchAttr" in w for w in carb.warnings))

    def test_schema_attr_present_returns_true(self):
        class _Attr:
            def __init__(self):
                self.value = None

            def Set(self, value):
                self.value = value

        class Schema:
            def __init__(self):
                self._attr = _Attr()

            def GetSpeedAttr(self):
                return self._attr

            def CreateSpeedAttr(self):
                return self._attr

        self.assertTrue(SimWarden._set_schema_attr(Schema(), "Speed", 42, "PhysX"))


if __name__ == "__main__":
    unittest.main()
