"""ENF source: mains-hum frequency vs grid-frequency records.

Modules: ``extract`` (find the hum, follow its frequency), ``match`` (correlate against a
reference), ``references`` (public grid-frequency archives and CSV imports), ``gridmap``
(mains frequency per country), ``service`` (case files and jobs), ``synth`` and ``demo``.
"""
from .demo import make_demo_case

__all__ = ["make_demo_case"]
