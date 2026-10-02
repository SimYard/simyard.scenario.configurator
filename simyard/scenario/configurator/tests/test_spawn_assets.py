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

"""
Loads config/scene/sample_scene.yaml (a cube asset with both static `instances` and
a Replicator `randomizers` entry enabled) and feeds it into
simyard.scenario.configurator.impl.spawn_assets to check that cubes get spawned
onto the context USD stage as described by the config, referencing the real
assets/shapes/cube.usda asset, and that the randomizer's Replicator graph samples
poses within the configured distributions.
"""

import copy
from pathlib import Path

import omni.kit.test
import omni.replicator.core as rep
import omni.usd
import yaml
from pxr import UsdGeom

from ..impl.spawn_assets import spawn_assets

REPO_ROOT = Path(__file__).resolve().parents[4]
SAMPLE_SCENE_PATH = REPO_ROOT / "config" / "scene" / "sample_scene.yaml"
CUBE_PRIM_PATH = "/World/Shapes/cube"


def _xform_ops(prim):
    return {op.GetOpName(): op.Get() for op in UsdGeom.Xformable(prim).GetOrderedXformOps()}


class TestSpawnAssets(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        with open(SAMPLE_SCENE_PATH) as scene_file:
            self.scene_config = yaml.safe_load(scene_file)
        for asset_config in self.scene_config["assets"]:
            asset_config["usd"] = str(REPO_ROOT / asset_config["usd"])
        self.cube_config = self.scene_config["assets"][0]
        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()

    async def test_static_and_randomized_instances_are_spawned_together(self):
        prim_paths = spawn_assets(self.scene_config)

        # 2 static instances (ids 0, 1) + 2 randomized instances (ids 2, 3), since
        # the sample config enables both for the cube asset.
        self.assertEqual(prim_paths, [f"{CUBE_PRIM_PATH}/instance_{i}" for i in range(4)])
        for prim_path in prim_paths:
            prim = self.stage.GetPrimAtPath(prim_path)
            self.assertTrue(prim.IsValid())
            # The reference to assets/shapes/cube.usda resolved and its `size`
            # attribute composed onto the instance prim.
            self.assertEqual(UsdGeom.Cube(prim).GetSizeAttr().Get(), 1.0)

    async def test_disabled_static_instance_is_skipped_without_renumbering(self):
        self.cube_config["randomizers"][0]["enabled"] = False
        self.cube_config["instances"][0]["enabled"] = False

        prim_paths = spawn_assets(self.scene_config)

        self.assertEqual(prim_paths, [f"{CUBE_PRIM_PATH}/instance_1"])

    async def test_disabling_randomizers_leaves_only_static_instances(self):
        self.cube_config["randomizers"][0]["enabled"] = False

        prim_paths = spawn_assets(self.scene_config)

        self.assertEqual(prim_paths, [f"{CUBE_PRIM_PATH}/instance_0", f"{CUBE_PRIM_PATH}/instance_1"])

    async def test_static_pose_and_semantics_are_applied(self):
        spawn_assets(self.scene_config)

        prim = self.stage.GetPrimAtPath(f"{CUBE_PRIM_PATH}/instance_1")
        ops = _xform_ops(prim)
        self.assertEqual(tuple(ops["xformOp:translate"]), (2.0, 0.0, 0.0))
        self.assertEqual(tuple(ops["xformOp:rotateXYZ"]), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(ops["xformOp:scale"]), (1.0, 1.0, 1.0))

        # Replicator-readable semantics (SemanticsAPI), not a custom attribute.
        self.assertIn("SemanticsAPI:class_cube", prim.GetAppliedSchemas())
        self.assertEqual(prim.GetAttribute("semantic:class_cube:params:semanticType").Get(), "class")
        self.assertEqual(prim.GetAttribute("semantic:class_cube:params:semanticData").Get(), "cube")

    async def test_static_scale_does_not_move_the_instance(self):
        self.cube_config["instances"][1]["pose"]["scale"] = 0.5

        spawn_assets(self.scene_config)

        prim = self.stage.GetPrimAtPath(f"{CUBE_PRIM_PATH}/instance_1")
        world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
        self.assertEqual(tuple(world.ExtractTranslation()), (2.0, 0.0, 0.0))

    async def test_randomized_poses_are_sampled_within_configured_distributions(self):
        pose_config = self.cube_config["randomizers"][0]["randomize"][0]["modify.pose"]
        position = pose_config["position"]["distribution.uniform"]
        scale = pose_config["scale"]["distribution.uniform"]

        prim_paths = spawn_assets(self.scene_config)
        await rep.orchestrator.step_async()

        translates = []
        for prim_path in prim_paths[2:]:
            ops = _xform_ops(self.stage.GetPrimAtPath(prim_path))
            translate = ops["xformOp:translate"]
            translates.append(tuple(translate))
            for axis in range(3):
                self.assertTrue(position["lower"][axis] <= translate[axis] <= position["upper"][axis])
            self.assertTrue(scale["lower"] <= ops["xformOp:scale"][0] <= scale["upper"])
        # Replicator samples per prim, so the two randomized instances differ.
        self.assertNotEqual(translates[0], translates[1])

    async def test_randomized_poses_are_deterministic_for_given_seeds(self):
        def randomized_translates():
            return [
                tuple(_xform_ops(self.stage.GetPrimAtPath(f"{CUBE_PRIM_PATH}/instance_{i}"))["xformOp:translate"])
                for i in (2, 3)
            ]

        spawn_assets(copy.deepcopy(self.scene_config))
        await rep.orchestrator.step_async()
        first = randomized_translates()

        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()
        spawn_assets(copy.deepcopy(self.scene_config))
        await rep.orchestrator.step_async()

        self.assertEqual(randomized_translates(), first)

    async def test_unknown_replicator_function_is_rejected(self):
        self.cube_config["randomizers"][0]["randomize"] = [{"os.system": {}}]

        with self.assertRaises(ValueError):
            spawn_assets(self.scene_config)
