# -*- coding: utf-8 -*-
"""NON-INVASIVE verification harness for RM001.

Three confirmed bugs in hc_agent/ block the official command:

  1. research_pipeline.py imports POLICY_ATC / POLICY_ATC_DRO / POLICY_FIXED
     from hc_agent.coordination.atc, but they are defined in
     hc_agent.research.capacity_outer.  -> ImportError at module import.

  2. capacity_outer.CapacityPoint declares `status: str` with NO default, but
     research_pipeline.py:444 constructs it without `status`.
     -> TypeError on the very first capacity trial.

  3. (see the run log) further issues are surfaced one at a time by the
     pipeline's broad `except Exception` handler.

This harness does NOT modify any repository source file. It monkey-patches the
loaded modules in memory so that the *input package* can be fully verified.
Run:

    python tests/research/RM001/_verify_run.py --inputs tests/research/RM001 \
        --output runs/RM001_CHECK
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---- fix 1: re-export the policy constants onto coordination.atc ----------
from hc_agent.research.capacity_outer import (  # noqa: E402
    POLICY_ATC, POLICY_ATC_DRO, POLICY_FIXED,
)
import hc_agent.coordination.atc as _atc  # noqa: E402

for _n, _v in (("POLICY_ATC", POLICY_ATC),
               ("POLICY_ATC_DRO", POLICY_ATC_DRO),
               ("POLICY_FIXED", POLICY_FIXED)):
    if not hasattr(_atc, _n):
        setattr(_atc, _n, _v)

# ---- fix 2: give CapacityPoint.status a default ---------------------------
import dataclasses  # noqa: E402
import hc_agent.research.capacity_outer as _co  # noqa: E402

if _co.CapacityPoint.__dataclass_fields__["status"].default is dataclasses.MISSING:
    _co.CapacityPoint = dataclasses.dataclass(
        type("CapacityPoint", (_co.CapacityPoint,), {
            "__annotations__": {"status": str},
            "status": _co.UNKNOWN,
        })
    )
    # keep the module attribute consistent for downstream imports
    import hc_agent.research_pipeline as _rp  # noqa: E402
    _rp.CapacityPoint = _co.CapacityPoint

from hc_agent.research_pipeline import main  # noqa: E402

# ---- fix 3: checkpoint(step, status, **extra) called with status=... ------
# research_pipeline.py:486 passes status= as a keyword while also passing the
# positional status, so every R03+ checkpoint raises TypeError.  Wrap it.
import hc_agent.research_pipeline as _rp  # noqa: E402

_orig_checkpoint = _rp.ResearchPipeline.checkpoint


def _safe_checkpoint(self, step, step_status, **extra):
    # callers pass a positional status AND sometimes status= as a keyword
    if "status" in extra:
        extra["outer_status"] = extra.pop("status")
    if isinstance(step_status, str) and step_status in ("ok", "skipped",
                                                        "failed"):
        return _orig_checkpoint(self, step, step_status, **extra)
    return _orig_checkpoint(self, step, extra.pop("outer_status", "ok"),
                            **extra)


_rp.ResearchPipeline.checkpoint = _safe_checkpoint

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
