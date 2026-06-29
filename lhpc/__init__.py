"""LoRaHAM Pi Control (lhpc).

Terminal and web installer, updater, configurator and orchestrator for LoRaHAM Pi
stacks. A shared core holds all behaviour; thin CLI and web adapters call into it.
"""

from .version import __version__

__all__ = ["__version__"]
