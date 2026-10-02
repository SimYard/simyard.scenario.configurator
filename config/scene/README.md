# Scene Configurator

The scene configurator spawns USD assets into an Isaac Sim stage from a YAML file and
randomizes them with [Omniverse Replicator](https://docs.omniverse.nvidia.com/extensions/latest/ext_replicator.html)
(`omni.replicator.core`).

It is a thin, declarative layer over Replicator rather than a separate randomization engine:

- **Static instances** are written straight to USD with a fixed pose.
- **Randomizers** compile to a Replicator graph: `rep.create.group` + `rep.trigger.*` +
  `rep.modify.*` / `rep.randomizer.*` ops with `rep.distribution.*` values.
- Config keys use Replicator's own function and argument names, so the Replicator API docs
  apply directly to the YAML.

Implementation: [`simyard/scenario/configurator/impl/spawn_assets.py`](../../simyard/scenario/configurator/impl/spawn_assets.py)
Example config: [`sample_scene.yaml`](sample_scene.yaml)

This file covers asset spawning and randomization only. Scene- and environment-level
settings (lighting, ground plane, physics world, etc.) belong in a separate config.

## Requirements

- Isaac Sim 4.5 (Kit 106.5, Replicator 1.11). Other versions are untested.
- The `simyard.scenario.configurator` extension enabled. It depends on `omni.replicator.core`
  and `omni.usd`, which Kit loads automatically.

## Config reference

A scene config has a top-level `assets` list. Each asset entry:

| Key           | Type                    | Description |
|---------------|-------------------------|-------------|
| `name`        | string                  | Human-readable name. |
| `usd`         | string                  | Path to the asset's USD file (same meaning as `rep.create.from_usd(usd=...)`). Relative paths resolve against the working directory, so prefer absolute paths or launch from the repo root. |
| `prim_path`   | string                  | Parent prim. Every instance is created as `<prim_path>/instance_<id>`. |
| `semantics`   | list of `[type, value]` | Replicator semantic labels, applied with the Semantics schema so annotators (segmentation, bounding boxes, ...) can read them. |
| `variants`    | map                     | `{variant_set: selection}` applied to every instance. |
| `instances`   | list                    | Static instances (see below). |
| `randomizers` | list                    | Randomized instance groups (see below). |

### Static instances

```yaml
instances:
  - enabled: true
    pose:
      position: [2.0, 0.0, 0.0]   # world units
      rotation: [0.0, 0.0, 90.0]  # degrees, XYZ order
      scale: [1.0, 1.0, 1.0]      # or a single number for uniform scale
```

`pose` uses the same keys as `rep.modify.pose`. It is written as `translate`, `rotateXYZ`,
`scale` xform ops, the same ops Replicator writes, so scaling never moves the instance.
Missing keys default to the identity.

### Randomizers

```yaml
randomizers:
  - enabled: true
    count: 5                              # instances in this group
    trigger:                              # exactly one rep.trigger.<name>: {kwargs}
      on_frame: {interval: 1}
    randomize:                            # rep.modify.* / rep.randomizer.* ops, applied in order
      - modify.pose:
          position:
            distribution.uniform: {lower: [-3, -3, 0], upper: [3, 3, 0], seed: 7}
          rotation:
            distribution.uniform: {lower: [0, 0, 0], upper: [0, 0, 360]}
          scale:
            distribution.normal: {mean: 1.0, std: 0.1}
      - modify.visibility:
          value:
            distribution.choice: {choices: [true, false]}
```

Each enabled randomizer spawns `count` prims under `prim_path`, groups them with
`rep.create.group`, and runs its `randomize` ops on that group whenever the trigger fires.

- **`trigger`**: any `rep.trigger` function, e.g. `on_frame`, `on_time`, `on_custom_event`.
  `{on_frame: {max_execs: 1}}` samples once.
- **`randomize`**: each item is `{<namespace>.<function>: {kwargs}}`. Only the `modify` and
  `randomizer` namespaces are allowed, for example `modify.pose`, `modify.visibility`,
  `modify.variant`, `randomizer.scatter_2d`, `randomizer.materials`.
- **Distributions**: any argument value written as `{distribution.<name>: {kwargs}}` becomes
  `rep.distribution.<name>(**kwargs)` (`uniform`, `normal`, `choice`, `sequence`,
  `log_uniform`, ...). It works at any nesting depth. Replicator samples per prim, so each
  instance in a group gets its own value.
- **Seeds**: set a nonzero `seed` on a distribution for reproducible samples. Replicator
  treats `seed: 0` as unset and falls back to the global seed (`rep.set_global_seed`).

An unknown or disallowed function name, or a randomizer without a `trigger`, raises
`ValueError` when the scene is spawned.

### Instance ids

Static instances take ids `0 .. len(instances) - 1` by list position, whether enabled or
not, so toggling `enabled` never renames the others. Randomizer instances continue from
`len(instances)`, in randomizer order. Disabled entries spawn nothing.

## Usage

`spawn_assets(scene_config, stage=None)` spawns every asset and returns the created prim
paths. `stage` defaults to the current `omni.usd` context stage, which is the stage
Replicator builds its graph on.

Static instances are posed as soon as `spawn_assets` returns. Randomized instances stay at
the origin until the Replicator graph runs:

- `await rep.orchestrator.step_async()` (or `rep.orchestrator.step()`) draws one sample,
- `rep.orchestrator.run()` keeps running until all triggers reach their `max_execs`,
- Replicator → Preview in the GUI.

### From the Isaac Sim GUI

1. In **Window → Extensions → ⚙ → Extension Search Paths**, add the folder that contains
   `simyard.scenario.configurator`, then enable the extension.
2. Run this in **Window → Script Editor**:

```python
import asyncio
from pathlib import Path

import omni.replicator.core as rep
import omni.usd
import yaml
from simyard.scenario.configurator.impl.spawn_assets import spawn_assets

REPO = Path("/path/to/simyard.scenario.configurator")


async def main():
    await omni.usd.get_context().new_stage_async()
    config = yaml.safe_load((REPO / "config/scene/sample_scene.yaml").read_text())
    for asset in config["assets"]:
        asset["usd"] = str(REPO / asset["usd"])

    print(spawn_assets(config))          # creates prims and builds the Replicator graph
    await rep.orchestrator.step_async()  # fires triggers, samples randomized poses


asyncio.ensure_future(main())
```

### Writing a dataset

Cameras and writers are plain Replicator and sit alongside the configurator. Attach them
before stepping:

```python
camera = rep.create.camera(position=(0, -10, 5), look_at=(0, 0, 0))
render_product = rep.create.render_product(camera, (1024, 1024))
writer = rep.WriterRegistry.get("BasicWriter")
writer.initialize(output_dir="/tmp/simyard_out", rgb=True,
                  semantic_segmentation=True, bounding_box_2d_tight=True)
writer.attach([render_product])
```

## Running the tests

The tests are Kit async tests and need a running Kit app. Plain `python -m unittest`
does not work. Kit discovers extensions by folder, so link the repo into an extension folder
first:

```bash
mkdir -p ~/simyard_exts
ln -sfn /path/to/simyard.scenario.configurator ~/simyard_exts/simyard.scenario.configurator

cd /path/to/simyard.scenario.configurator
~/isaac-sim/kit/kit ~/isaac-sim/apps/isaacsim.exp.base.kit \
  --no-window \
  --ext-folder ~/simyard_exts \
  --enable omni.kit.test \
  --/exts/omni.kit.test/testExts/0=simyard.scenario.configurator \
  --/exts/omni.kit.test/runTestsAndQuit=true \
  --/app/fastShutdown=true
```

A passing run ends with `EXTENSION TEST PASSED: simyard.scenario.configurator` and exits
with code 0. The first run takes a few minutes while Kit warms its caches. To run only the
configurator tests, add `--/exts/omni.kit.test/runTestsFilter=*spawn*`.

Test arguments from `config/extension.toml` are applied automatically. That includes
`fabricDefaultStageFrameHistoryCount=3`, without which `omni.syntheticdata` (a Replicator
dependency) logs a startup error that fails the run.

The configurator tests live in
[`simyard/scenario/configurator/tests/test_spawn_assets.py`](../../simyard/scenario/configurator/tests/test_spawn_assets.py).
They check:

- static and randomized instances are spawned together, with ids in order,
- disabled entries are skipped without renumbering the others,
- static poses, xform op order and Replicator semantics,
- randomized poses fall within their distributions and are reproducible for a given seed,
- disallowed Replicator functions are rejected.
