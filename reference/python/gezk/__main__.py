"""`python -m gezk` — the console script's entry point, reachable without one.

An editable install from a checkout is the supported way to run this reader
(there is no PyPI release), and `python -m` addresses it by import path rather
than by whatever ended up on PATH.
"""

import sys

from .cli import main

sys.exit(main())
