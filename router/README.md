# H7-P offline model router

`universal_model_router.py` selects from saved benchmark profiles. It does not
call an endpoint, inspect hardware, start a model, change placement, or publish
a route.

Inputs:

- One exact `model-task-suite/v1`.
- One `model-route-request/v1`.
- One or more `model-benchmark-profile/v1` documents.
- Each profile embeds one complete `model-test-configuration/v1`.

The selector first applies hard constraints. It checks suite identity,
evidence age, hard gates, task floors, capabilities, trust zone, workload
envelope, latency, energy, GPUs, RAM, request reliability, and restoration.
It then sorts eligible routes by the request's explicit objective order.

Vertical, horizontal, and hybrid points are separate profiles. The scaling
series and point IDs preserve their relationship without merging evidence.

Run the focused tests:

```powershell
python -m unittest -v test_universal_model_router.py
```

Run the offline selector with saved files:

```powershell
python universal_model_router.py request.json standard-task-suite-v1.json profile-a.json profile-b.json
```

A decision has saved-data authority only. H7 must separately verify the active
route manifest, live identity, health, failover, publication, and lifecycle
boundaries.
