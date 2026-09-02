"""Voice-activity adapters. `EnergyVad` needs nothing; `SileroVad` needs the `ml` extra.

`SileroVad` is deliberately NOT imported here: this package is imported on every CI run,
where torch does not exist. Import it from `readycall.adapters.vad.silero` at the point of
use, which is `build_vad` in the container.
"""

from readycall.adapters.vad.energy import EnergyVad

__all__ = ["EnergyVad"]
