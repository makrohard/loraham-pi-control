"""LHPC MeshCore host application: openHop Core + LoRaHAM daemon integration.

Shipped as LHPC package data and pip-installed into the MeshCore stack venv at build
time. Owns host policy (config, identity loading, GPS feed, persistence wiring,
readiness); all MeshCore protocol behaviour lives in openhop_core.
"""

__all__ = ["loraham_radio"]
