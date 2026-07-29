# SimYard Scenario Configurator

Ships `SimWarden` — applies a config file's physics and rendering parameters to the running
Isaac Sim stage — plus a `Tools > SimYard > Open Config` menu entry to pick a config and apply it.

## Install

Isaac Sim only loads extensions on an **extension search folder**. Either:

- **Drop it into `extsUser/`** (auto-scanned): copy or symlink `simyard.scenario.configurator`
  into `<isaac-sim>/extsUser/`. For a source build that folder is
  `_build/<platform>/release/extsUser/`.
- **Or point Isaac at it:** launch with `--ext-folder /path/to/extsUser`, or add the path via
  Window > Extensions > ☰ > *Settings*.

## Enable

Window > Extensions > search `simyard` > toggle on (enable **AUTOLOAD** to persist).

## Use

**Tools > SimYard > Open Config** opens the native file browser to pick a `.yaml`/`.json` config.
On selection it is applied to the current stage via `SimWarden` and the result — including any
keys that could not be resolved — is shown as a notification.

`SimWarden` also works standalone: `SimWarden.from_file(cfg).apply(world)` after `SimulationApp`.
