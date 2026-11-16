"""What goes over the WebSocket must be valid JSON for a browser."""
import json

import numpy as np

from lightlag.api.app import dumps


def test_non_finite_floats_become_null():
    # TERN logs time-to-harm as infinity when there is no deadline; JSON.parse rejects "Infinity"
    out = dumps({"tth": float("inf"), "x": [1.5, float("nan"), -float("inf")], "n": np.float64(2.0)})
    assert json.loads(out) == {"tth": None, "x": [1.5, None, None], "n": 2.0}
    assert "Infinity" not in out and "NaN" not in out
