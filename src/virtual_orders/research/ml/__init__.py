"""Phase 2 of the research engine: a small, auditable classifier over the deterministic feature snapshots.

Everything above this package works without it. The deterministic detection, the context engine and the
leak-safe backtester are the product; a model is an opinion layered on top of them, and it is kept behind its
own package so that removing it would break nothing else.

Three rules hold here:

* **No new dependency.** The model is a depth-limited gradient-boosted tree ensemble implemented on NumPy,
  which the platform already ships. It trains on a laptop in seconds, needs no GPU, and a stored model is a
  JSON document a human can read rather than an opaque binary.
* **No randomness.** There is no subsampling and no shuffling, so the same rows and the same hyperparameters
  always produce byte-identical trees. Reproducibility outranks the last fraction of accuracy.
* **One operational question.** The target is `P(target reached before stop)`, not "up or down": a probability
  that maps onto the barriers the backtester actually measured.
"""
