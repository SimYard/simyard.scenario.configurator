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
Spawns the USD assets described by a parsed scene config (see config/scene/*.yaml)
into the current USD stage, and compiles their randomizers into a Replicator graph.

Each asset entry produces two additive sources of instances:
  - `instances`: static prims with a fixed pose, written directly to USD.
  - `randomizers`: `count` prims per entry, grouped with rep.create.group, whose
    `randomize` ops (rep.modify.* / rep.randomizer.*) run under the entry's rep.trigger.*.
Only entries with `enabled: true` are spawned. ids are assigned by position: static
instances take 0..len(instances)-1, and randomizer instances continue from there.

Randomizers only build the Replicator graph; poses are sampled when the graph runs,
e.g. via rep.orchestrator.step_async() or rep.orchestrator.run().
"""

import omni.replicator.core as rep
import omni.usd
from pxr import Gf, Sdf, UsdGeom

# Replicator namespaces a config may call into, for ops and argument values respectively.
_OP_NAMESPACES = ("modify", "randomizer")
_DISTRIBUTION_PREFIX = "distribution."


def _resolve_rep_function(dotted_name, namespaces):
    """Map a config key such as `modify.pose` to the matching omni.replicator.core function."""
    namespace, _, function_name = dotted_name.partition(".")
    if namespace not in namespaces or not function_name or function_name.startswith("_"):
        raise ValueError(f"Unsupported Replicator function `{dotted_name}`; expected one of {namespaces}.<name>")
    function = getattr(getattr(rep, namespace), function_name, None)
    if function is None:
        raise ValueError(f"Replicator has no function `rep.{dotted_name}`")
    return function


def _single_call(spec, what):
    """Unpack a `{name: kwargs}` mapping with exactly one entry."""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"Each {what} must be a single `name: {{kwargs}}` mapping, got {spec!r}")
    (name, kwargs), = spec.items()
    return name, kwargs or {}


def _resolve_value(value):
    """Turn `{distribution.<name>: kwargs}` values (at any depth) into rep.distribution.* nodes."""
    if isinstance(value, dict):
        if len(value) == 1:
            (key, kwargs), = value.items()
            if isinstance(key, str) and key.startswith(_DISTRIBUTION_PREFIX):
                distribution = _resolve_rep_function(key, ("distribution",))
                return distribution(**_resolve_value(kwargs or {}))
        return {key: _resolve_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item) for item in value]
    return value


def _set_static_pose(prim, pose):
    """Write translate/rotateXYZ/scale xform ops, the same ops rep.modify.pose writes."""
    scale = pose.get("scale", 1.0)
    if not isinstance(scale, (list, tuple)):
        scale = (scale, scale, scale)

    xformable = UsdGeom.Xformable(prim)
    xformable.ClearXformOpOrder()
    xformable.AddTranslateOp().Set(Gf.Vec3d(*pose.get("position", (0.0, 0.0, 0.0))))
    xformable.AddRotateXYZOp().Set(Gf.Vec3f(*pose.get("rotation", (0.0, 0.0, 0.0))))
    xformable.AddScaleOp().Set(Gf.Vec3f(*scale))


def _spawn_instance(stage, asset_config, instance_id, pose):
    """Create one referenced instance prim under the asset's `prim_path` and return its path."""
    prim_path = Sdf.Path(asset_config["prim_path"]).AppendChild(f"instance_{instance_id}")
    prim = stage.DefinePrim(prim_path, "Xform")
    prim.GetReferences().AddReference(asset_config["usd"])

    for variant_set, variant in asset_config.get("variants", {}).items():
        prim.GetVariantSets().GetVariantSet(variant_set).SetVariantSelection(variant)

    _set_static_pose(prim, pose)
    return str(prim_path)


def _build_randomizer(group, randomizer_config):
    """Attach a randomizer's `randomize` ops to `group` under its configured rep.trigger.*."""
    if "trigger" not in randomizer_config:
        raise ValueError("Each randomizer needs a `trigger`, e.g. `trigger: {on_frame: {max_execs: 1}}`")
    trigger_name, trigger_kwargs = _single_call(randomizer_config["trigger"], "trigger")
    trigger = getattr(rep.trigger, trigger_name, None)
    if trigger is None or trigger_name.startswith("_"):
        raise ValueError(f"Replicator has no trigger `rep.trigger.{trigger_name}`")

    with trigger(**trigger_kwargs):
        with group:
            for op_spec in randomizer_config.get("randomize", []):
                op_name, op_kwargs = _single_call(op_spec, "randomize op")
                _resolve_rep_function(op_name, _OP_NAMESPACES)(**_resolve_value(op_kwargs))


def spawn_asset(stage, asset_config):
    """Spawn every enabled static and randomized instance of one asset entry.

    Returns the list of USD prim paths (as strings) that were created, in id order.
    """
    instances = asset_config.get("instances", [])
    prim_paths = [
        _spawn_instance(stage, asset_config, instance_id, instance.get("pose", {}))
        for instance_id, instance in enumerate(instances)
        if instance.get("enabled", True)
    ]

    next_id = len(instances)
    randomized_groups = []
    for randomizer_config in asset_config.get("randomizers", []):
        if not randomizer_config.get("enabled", True):
            continue
        group_paths = [
            _spawn_instance(stage, asset_config, instance_id, {})
            for instance_id in range(next_id, next_id + randomizer_config["count"])
        ]
        next_id += randomizer_config["count"]
        prim_paths.extend(group_paths)
        randomized_groups.append((group_paths, randomizer_config))

    semantics = [tuple(pair) for pair in asset_config.get("semantics", [])]
    if semantics and prim_paths:
        # rep.create.group applies its semantics to the member prims immediately (SemanticsAPI).
        rep.create.group(prim_paths, semantics=semantics)

    for group_paths, randomizer_config in randomized_groups:
        if group_paths:
            _build_randomizer(rep.create.group(group_paths), randomizer_config)

    return prim_paths


def spawn_assets(scene_config, stage=None):
    """Spawn every asset described in a parsed scene config (see config/scene/*.yaml).

    `stage` defaults to the current omni.usd context stage, which is the stage Replicator
    builds its graph on; pass another stage only for configs without randomizers.
    Returns the list of USD prim paths (as strings) that were created, across all assets.
    """
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    prim_paths = []
    for asset_config in scene_config.get("assets", []):
        prim_paths.extend(spawn_asset(stage, asset_config))
    return prim_paths
