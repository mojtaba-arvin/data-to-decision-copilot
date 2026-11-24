# Spec 0008: evidence-to-decision traceability

## Scope

Create an immutable, verifiable record that links a user question, planner and policy versions, decision claims, and the exact executed evidence supporting those claims. This phase adds core contracts and deterministic construction/verification; it does not add an LLM planner, API, UI, or automatic actions.

## Trace contract

A decision trace contains:
- the original question and unique trace ID;
- planner and decision-policy version identifiers;
- a timezone-aware creation timestamp;
- the immutable decision contract;
- individually identified claims, each requiring one or more evidence IDs;
- evidence bindings containing evidence ID, query/result digests, source ID, schema fingerprint, and optional data version;
- a canonical SHA-256 digest over the complete trace payload (excluding the digest field itself).

The union of claim evidence references must exactly equal both the decision evidence IDs and trace evidence bindings. A claim without evidence, an unreferenced binding, duplicate IDs, or an absent binding fails closed.

## Construction and verification

Construction first verifies the evidence journal, then resolves each decision evidence ID from the journal and captures its provenance. It never accepts model-authored query/result digests or source metadata.

Verification recomputes the trace digest, verifies the complete evidence journal, resolves every evidence binding, and compares all captured provenance fields. Callers may supply current schema fingerprints by source ID; missing or changed fingerprints are treated as stale and invalidate the trace.

## Trust boundaries and limitations

SHA-256 is an integrity checksum, not a signature or proof of who created the trace. The trace is an immutable data contract in application memory/serialized form; this phase does not persist decision traces, sign them, anchor them externally, or protect against a fully compromised process or database. Planner/policy version strings are caller-supplied identifiers and must be tied to immutable deployed artifacts by a future runtime registry. Schema freshness is only checked when the caller supplies current fingerprints. Evidence proves captured query execution metadata, not the semantic truth of a recommendation. Human review remains necessary for consequential decisions.
