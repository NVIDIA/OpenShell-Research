# Validation sequence

---

**Parse the request.** Reject malformed input before gate evaluation.

**Apply policy.** Evaluate the configured gates in a stable order.

**Return the result.** Report the first denial with its concrete reason.
