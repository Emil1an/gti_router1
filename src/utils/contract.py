"""Cross-system exposure contract — "no detection" marking (Story 6.4, AR8).

Ecosystem invariant: **the Router only produces raw video / last-frame WITHOUT
detection**. All inference (fire/smoke) is the Gateway's responsibility. GTI
Satélites must be able to tell the two origins apart, so everything the Router
publishes (the last-frame snapshot of Story 6.3, the DJI feed of Story 5.3)
carries an explicit, versioned origin marking:

* ``source = "router"``  ⇒ **no detection** (raw view).
* ``source = "gateway"`` ⇒ **with detection** (produced elsewhere).

This is the single, centralised definition so every Router producer inherits the
same marking — never duplicated ad-hoc. The Router contains **no** detection
code or model dependency (guarded by ``tests/test_no_detection_contract.py``).
"""

from __future__ import annotations

# Origin values for the cross-system contract.
ROUTER_SOURCE = "router"      # ⇒ raw view, NO detection
GATEWAY_SOURCE = "gateway"    # ⇒ frames WITH detection (not produced here)

# Versioned so Satélites can evolve the contract without breaking.
CONTRACT_VERSION = "1.0"


def no_detection_contract() -> dict[str, str]:
    """Return the canonical origin/version marking for Router-published images.

    Values are plain strings so the dict can be attached directly as S3 object
    metadata. It deliberately carries **no** detection fields — ``source=router``
    is itself the "no detection" guarantee.
    """
    return {"source": ROUTER_SOURCE, "contract_version": CONTRACT_VERSION}
