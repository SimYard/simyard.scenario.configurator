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

"""SimWarden - apply declarative physics and rendering parameters to Isaac Sim.

The warden reads a configuration with two optional sections and applies each to
the running simulation:

* ``rendering`` - key/value pairs written to ``carb.settings`` (RTX / renderer).
* ``physics``   - dispatched across the high-level ``PhysicsContext``, the USD
  ``PhysicsScene`` prim and the PhysX ``PhysxSceneAPI``.

Typical use from a standalone launch script::

    from isaacsim import SimulationApp
    app = SimulationApp({"headless": False})
    from isaacsim.core.api import World
    from simyard.scenario.configurator.sim_warden import SimWarden

    world = World()
    SimWarden.from_file("configs/base_config.yaml").apply(world)
    world.reset()

or from inside the extension against the current stage::

    SimWarden(config).apply()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import carb
except ImportError:  # Allow importing the module outside a running Kit app.
    carb = None

__all__ = ["SimWarden", "apply_config"]

#: Config section names understood by the warden.
_RENDERING_KEYS = ("rendering", "global_settings")
_PHYSICS_KEYS = ("physics",)

#: Candidate prim paths searched when locating an existing physics scene.
_PHYSICS_SCENE_PATHS = ("/World/PhysicsScene", "/physicsScene", "/PhysicsScene")


def _log_info(message: str) -> None:
    if carb is not None:
        carb.log_info(message)
    else:
        print(message)


def _log_warn(message: str) -> None:
    if carb is not None:
        carb.log_warn(message)
    else:
        print(f"Warning: {message}")


def _log_error(message: str) -> None:
    if carb is not None:
        carb.log_error(message)
    else:
        print(f"Error: {message}")


def _convert_value(value: Any) -> Any:
    """Coerce a config value into a USD/PhysX-friendly type.

    Three-element sequences become ``Gf.Vec3f``; string booleans become ``bool``.
    Everything else is returned unchanged.

    Args:
        value: The raw value taken from the configuration.

    Returns:
        The converted value, or the original value when no conversion applies.
    """
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            from pxr import Gf

            return Gf.Vec3f(float(value[0]), float(value[1]), float(value[2]))
        except (ImportError, ValueError, TypeError):
            return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def apply_config(config: dict[str, Any], world: Any = None) -> None:
    """Apply a physics/rendering config in one call.

    Convenience wrapper around ``SimWarden(config).apply(world)``.

    Args:
        config: Configuration dictionary with ``physics`` and/or ``rendering`` sections.
        world: Optional Isaac Sim ``World`` used to resolve the physics context and stage.
    """
    SimWarden(config).apply(world)


class SimWarden:
    """Applies physics and rendering parameters from a config to Isaac Sim.

    Args:
        config: Configuration dictionary. Recognised sections are ``physics`` and
            ``rendering`` (``global_settings`` is accepted as an alias of ``rendering``).
        world: Optional Isaac Sim ``World``. When provided it is used to resolve the
            physics context and the USD stage; otherwise the current app context is used.
    """

    def __init__(self, config: dict[str, Any] | None = None, world: Any = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._world = world

    @classmethod
    def from_file(cls, config_path: str | Path, world: Any = None) -> SimWarden:
        """Build a warden from a YAML or JSON config file.

        Args:
            config_path: Path to a ``.yaml``/``.yml`` or ``.json`` configuration file.
            world: Optional Isaac Sim ``World`` forwarded to the constructor.

        Returns:
            A ``SimWarden`` initialised with the parsed configuration.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file extension is not supported.
        """
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")

        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            import yaml

            config = yaml.safe_load(text) or {}
        elif suffix == ".json":
            config = json.loads(text)
        else:
            raise ValueError(f"Unsupported config format '{suffix}' (expected .yaml, .yml or .json)")

        return cls(config, world)

    def apply(self, world: Any = None) -> dict[str, list[str]]:
        """Apply rendering and physics settings from the configuration.

        Args:
            world: Optional ``World`` overriding the one passed at construction time.

        Returns:
            A report ``{"unknown_rendering_keys": [...], "unhandled_physics_keys": [...]}``
            listing config keys that could not be applied (typos or unavailable in this build).
        """
        if world is not None:
            self._world = world

        _log_info("SimWarden: applying configuration...")
        report = {
            "unknown_rendering_keys": self.apply_rendering(),
            "unhandled_physics_keys": self.apply_physics(),
        }
        unresolved = report["unknown_rendering_keys"] + report["unhandled_physics_keys"]
        if unresolved:
            _log_warn(f"SimWarden: {len(unresolved)} unresolved key(s): {', '.join(unresolved)}")
        _log_info("SimWarden: configuration applied.")
        return report

    def apply_rendering(self) -> list[str]:
        """Write the ``rendering`` section to ``carb.settings``.

        Returns:
            Keys that were not registered in this build (likely a typo or unavailable).
        """
        settings_dict = self._section(_RENDERING_KEYS)
        if not settings_dict:
            _log_info("SimWarden: no rendering settings in config, skipping.")
            return []
        if carb is None:
            _log_warn("SimWarden: carb is unavailable, cannot apply rendering settings.")
            return []

        settings = carb.settings.get_settings()
        unknown: list[str] = []
        _log_info(f"SimWarden: applying {len(settings_dict)} rendering settings...")
        for key, value in settings_dict.items():
            # A carb key that reads back None was never registered by any extension,
            # so it is almost certainly a typo or unavailable in this build.
            if settings.get(key) is None:
                unknown.append(key)
                _log_warn(f"SimWarden: rendering key not found (typo or unavailable): {key}")
            try:
                settings.set(key, value)
                _log_info(f"SimWarden: set {key} = {value}")
            except Exception as exc:  # noqa: BLE001 - carb.settings raises broadly
                _log_warn(f"SimWarden: failed to set rendering '{key}': {exc}")
        return unknown

    def apply_physics(self) -> list[str]:
        """Apply the ``physics`` section across the context, USD and PhysX APIs.

        Bare keys are dispatched to ``PhysicsContext`` methods. Keys prefixed with
        ``context.``, ``usd.`` or ``physx.`` target that specific API.

        Returns:
            Keys with no matching PhysicsContext method or USD/PhysX attribute.
        """
        physics = self._section(_PHYSICS_KEYS)
        if not physics:
            _log_info("SimWarden: no physics settings in config, skipping.")
            return []

        try:
            from pxr import PhysxSchema, UsdPhysics
        except ImportError as exc:
            _log_error(f"SimWarden: USD/PhysX modules unavailable, cannot apply physics: {exc}")
            return []

        context_settings, usd_settings, physx_settings = self._split_physics(physics)
        unhandled: list[str] = []

        # 1) High-level PhysicsContext (reflective method dispatch).
        physics_context = self._resolve_physics_context()
        if physics_context is not None and context_settings:
            unhandled += self._dispatch_methods(physics_context, context_settings)
        elif context_settings:
            _log_warn("SimWarden: no physics context available, skipping context settings.")

        # 2) USD / PhysX scene attributes.
        if not (usd_settings or physx_settings):
            return unhandled

        stage = self._resolve_stage()
        if stage is None:
            _log_warn("SimWarden: no stage available, skipping USD/PhysX scene settings.")
            return unhandled

        usd_scene, physx_api = self._find_physics_scene(stage, UsdPhysics, PhysxSchema)
        if usd_scene is None:
            usd_scene, physx_api = self._create_physics_scene(stage, UsdPhysics, PhysxSchema)

        if usd_scene is not None:
            for name, value in usd_settings.items():
                if not self._set_schema_attr(usd_scene, name, value, api_label="USD Physics"):
                    unhandled.append(f"usd.{name}")
        if physx_api is not None:
            for name, value in physx_settings.items():
                if not self._set_schema_attr(physx_api, name, value, api_label="PhysX"):
                    unhandled.append(f"physx.{name}")
        return unhandled

    def _section(self, keys: tuple[str, ...]) -> dict[str, Any]:
        """Return the first present config section among `keys`, or an empty dict."""
        for key in keys:
            section = self._config.get(key)
            if isinstance(section, dict) and section:
                return section
        return {}

    @staticmethod
    def _split_physics(physics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Split physics settings into (context, usd, physx) buckets by key prefix."""
        context_settings: dict[str, Any] = {}
        usd_settings: dict[str, Any] = {}
        physx_settings: dict[str, Any] = {}
        for key, value in physics.items():
            if key.startswith("context."):
                context_settings[key[len("context.") :]] = value
            elif key.startswith("usd."):
                usd_settings[key[len("usd.") :]] = value
            elif key.startswith("physx."):
                physx_settings[key[len("physx.") :]] = value
            else:
                context_settings[key] = value
        return context_settings, usd_settings, physx_settings

    def _resolve_physics_context(self) -> Any:
        """Resolve a ``PhysicsContext`` from the world, or a standalone fallback."""
        if self._world is not None and hasattr(self._world, "get_physics_context"):
            try:
                return self._world.get_physics_context()
            except Exception as exc:  # noqa: BLE001 - defensive across Isaac versions
                _log_warn(f"SimWarden: world.get_physics_context() failed: {exc}")
        try:
            from isaacsim.core.api.physics_context import PhysicsContext

            return PhysicsContext()
        except Exception as exc:  # noqa: BLE001 - Isaac core may be unavailable
            _log_warn(f"SimWarden: could not create a standalone PhysicsContext: {exc}")
            return None

    def _resolve_stage(self) -> Any:
        """Resolve the active USD stage from the world or the app context."""
        if self._world is not None and getattr(self._world, "stage", None) is not None:
            return self._world.stage
        try:
            import omni.usd

            return omni.usd.get_context().get_stage()
        except Exception as exc:  # noqa: BLE001 - omni.usd may be unavailable
            _log_warn(f"SimWarden: could not resolve a stage: {exc}")
            return None

    @staticmethod
    def _dispatch_methods(target: Any, settings_dict: dict[str, Any]) -> list[str]:
        """Call ``set_<key>`` or ``<key>`` on `target`; return keys with no matching method."""
        _log_info(f"SimWarden: applying {len(settings_dict)} physics context settings...")
        unhandled: list[str] = []
        for key, value in settings_dict.items():
            method = getattr(target, f"set_{key}", None) or getattr(target, key, None)
            if not callable(method):
                _log_warn(f"SimWarden: no PhysicsContext method for '{key}', skipping.")
                unhandled.append(key)
                continue
            try:
                method(value)
                _log_info(f"SimWarden: {target.__class__.__name__}.{method.__name__}({value})")
            except Exception as exc:  # noqa: BLE001 - Isaac setters raise broadly
                _log_warn(f"SimWarden: failed to apply physics '{key}': {exc}")
        return unhandled

    @staticmethod
    def _find_physics_scene(stage: Any, usd_physics: Any, physx_schema: Any) -> tuple[Any, Any]:
        """Locate an existing physics scene prim, returning (UsdScene, PhysxSceneAPI)."""
        # The typed schema class is `UsdPhysics.Scene`; older USD exposed it as `PhysicsScene`.
        scene_cls = getattr(usd_physics, "Scene", None) or getattr(usd_physics, "PhysicsScene", None)
        if scene_cls is None:
            _log_warn("SimWarden: UsdPhysics has neither 'Scene' nor 'PhysicsScene'.")
            return None, None
        for path in _PHYSICS_SCENE_PATHS:
            scene = scene_cls.Get(stage, path)
            prim = scene.GetPrim() if scene else None
            if prim is not None and prim.IsValid():
                return scene, physx_schema.PhysxSceneAPI.Get(stage, path)
        return None, None

    @staticmethod
    def _create_physics_scene(stage: Any, usd_physics: Any, physx_schema: Any) -> tuple[Any, Any]:
        """Define a physics scene at the first candidate path and apply PhysxSceneAPI."""
        scene_cls = getattr(usd_physics, "Scene", None) or getattr(usd_physics, "PhysicsScene", None)
        path = _PHYSICS_SCENE_PATHS[0]
        try:
            if scene_cls is None:
                raise AttributeError("UsdPhysics has neither 'Scene' nor 'PhysicsScene'")
            scene = scene_cls.Define(stage, path)
            physx_api = physx_schema.PhysxSceneAPI.Apply(scene.GetPrim())
            _log_info(f"SimWarden: created physics scene at {path}")
            return scene, physx_api
        except Exception as exc:  # noqa: BLE001 - USD authoring may fail
            _log_warn(f"SimWarden: failed to create physics scene: {exc}")
            return None, None

    @staticmethod
    def _set_schema_attr(schema: Any, name: str, value: Any, api_label: str) -> bool:
        """Set a USD/PhysX schema attribute; return False if the attribute does not exist."""
        getter = getattr(schema, f"Get{name}Attr", None)
        creator = getattr(schema, f"Create{name}Attr", None)
        if getter is None or creator is None:
            _log_warn(f"SimWarden: {api_label} attribute '{name}' is not available.")
            return False
        try:
            converted = _convert_value(value)
            attr = getter()
            if attr:
                attr.Set(converted)
            else:
                creator().Set(converted)
            _log_info(f"SimWarden: {api_label} {name} = {converted}")
            return True
        except Exception as exc:  # noqa: BLE001 - USD attribute authoring raises broadly
            _log_warn(f"SimWarden: failed to set {api_label} '{name}': {exc}")
            return False
