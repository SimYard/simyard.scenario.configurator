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

"""SimYard Scenario Configurator UI.

Adds a ``Tools > SimYard > Open Config`` menu entry that opens the native file browser, then
applies the chosen SimWarden config to the current stage. The outcome is reported as a transient
notification. The entry is backed by a registered action and ``onclick_action``.
"""

import os
from pathlib import Path

import carb
import omni.kit.actions.core
import omni.kit.menu.utils as menu_utils
from simyard.scenario.configurator.sim_warden import SimWarden

#: Config shipped with the extension, used as the default selection.
_DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "configs" / "base_config.yaml")

#: File-picker filter for SimWarden config files.
_CONFIG_EXTENSIONS = [(".yaml", "YAML config"), (".yml", "YAML config"), (".json", "JSON config")]

#: Top-level menu and submenu the entry lives under.
_MENU = "Tools"
_SUBMENU = "SimYard"

#: Action registered for the Open Config entry.
_ACTION_EXT = "simyard.scenario.configurator"
_ACTION_ID = "open_config"


class UIBuilder:
    """Adds a Tools > SimYard > Open Config menu item that opens and applies a SimWarden config."""

    def __init__(self):
        self._config_path = _DEFAULT_CONFIG

        omni.kit.actions.core.get_action_registry().register_action(
            _ACTION_EXT,
            _ACTION_ID,
            self._on_open_config,
            display_name="Open Config",
            description="Open a SimWarden config and apply it to the current stage",
            tag="Open Config",
        )
        # Our own "SimYard" submenu under Tools, following the convention other extensions use
        # (e.g. Tools > Animation > ...). We fully control the layout here.
        self._menu_items = [
            menu_utils.MenuItemDescription(
                name=_SUBMENU,
                sub_menu=[
                    menu_utils.MenuItemDescription(
                        name="Open Config",
                        onclick_action=(_ACTION_EXT, _ACTION_ID),
                    )
                ],
            )
        ]
        menu_utils.add_menu_items(self._menu_items, _MENU)

    def cleanup(self):
        """Remove the Tools > SimYard menu and its action."""
        menu_utils.remove_menu_items(self._menu_items, _MENU)
        self._menu_items = []
        try:
            omni.kit.actions.core.get_action_registry().deregister_action(_ACTION_EXT, _ACTION_ID)
        except Exception:  # noqa: BLE001 - action registry may already be torn down
            pass

    def _on_open_config(self, *args):
        """Open the native file browser to choose a SimWarden config."""
        from omni.kit.window.file_importer import get_file_importer

        file_importer = get_file_importer()
        if file_importer is None:
            self._notify("File importer is unavailable.", warn=True)
            return
        file_importer.show_window(
            title="Open SimWarden config",
            import_button_label="Open",
            import_handler=self._on_config_selected,
            file_extension_types=_CONFIG_EXTENSIONS,
            filename_url=os.path.dirname(self._config_path),
        )

    def _on_config_selected(self, filename, dirname, selections=None):
        """Apply the picked config (`import_handler` callback)."""
        path = selections[0] if selections else os.path.join(dirname or "", filename or "")
        if not path:
            return
        self._config_path = path
        self._apply(path)

    def _apply(self, path):
        """Apply a config file via SimWarden and report the outcome as a notification."""
        name = os.path.basename(path)
        try:
            report = SimWarden.from_file(path).apply()
            unresolved = report.get("unknown_rendering_keys", []) + report.get("unhandled_physics_keys", [])
            if unresolved:
                carb.log_warn(f"SimWarden UI: unresolved keys: {unresolved}")
                self._notify(f"Applied {name} — {len(unresolved)} unknown key(s): {', '.join(unresolved)}", warn=True)
            else:
                self._notify(f"Applied {name}")
            carb.log_info(f"SimWarden applied config: {path}")
        except Exception as exc:  # noqa: BLE001 - surface any load/apply error to the user
            carb.log_error(f"SimWarden apply failed for '{path}': {exc}")
            self._notify(f"SimWarden error: {exc}", warn=True)

    @staticmethod
    def _notify(text, warn=False):
        """Post a transient notification, falling back to the log if unavailable."""
        try:
            import omni.kit.notification_manager as nm

            status = nm.NotificationStatus.WARNING if warn else nm.NotificationStatus.INFO
            nm.post_notification(text, status=status)
        except Exception:  # noqa: BLE001 - notification manager may be unavailable
            (carb.log_warn if warn else carb.log_info)(f"SimWarden: {text}")
