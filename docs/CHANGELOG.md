# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added
- `SimWarden`: apply a config's `physics` and `rendering` sections to the running stage via
  `carb.settings`, `PhysicsContext`, `UsdPhysics`, and `PhysxSchema`. `apply()` returns a report
  of config keys it could not resolve (typos or settings unavailable in the current build).
- `configs/base_config.yaml`: bundled default physics/rendering config.
- Tools > SimYard > Open Config menu entry that picks a config via the native file browser and
  applies it to the current stage; the outcome, including unresolved keys, is shown as a notification.
