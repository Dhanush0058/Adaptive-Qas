"""Enforcement package – bridges Phase 3 policy to Linux tc.
Provides a safe, dry‑run capable interface for applying and rolling back QoS
configurations on a specific network interface.
"""

from .tc_controller import TcEnforcer
