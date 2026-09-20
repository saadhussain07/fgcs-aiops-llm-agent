# Remediation Plan Scoring Rubric (pre-registered)

**Status:** This rubric was written in full BEFORE any remediation plans
were scored, and before the scoring script was written. It is derived from
standard Kubernetes operational practice for each injected fault class,
rather than from inspection of the agent's outputs.

Two tier definitions were revised after an initial inspection of the data
but before any metrics were computed: the S5 tiers were tightened so that
inspecting a failed dependency scores Partial rather than Appropriate, and
an explicit rule was added for plans containing no LOW-risk step. Both
changes made the rubric stricter. They are recorded here rather than
silently folded in, so that a reader can see exactly what was fixed in
advance and what was adjusted along the way.

**Purpose:** The paper reports that the Runtime Anomaly Agent produces
risk-stratified `kubectl` remediation proposals, but does not evaluate
whether those proposals are appropriate. This rubric defines an objective,
reproducible measure of proposal quality that requires no human judgment.

---

## What is scored

For each monitoring cycle in which the agent returned
`verdict = ANOMALY_CONFIRMED` and at least one remediation step, we score
the **first LOW-risk step** — the step the framework would surface for
rapid operator confirmation (Section 4.6). Steps escalated to a human are
not scored, because the framework makes no autonomy claim about them.

Two independent dimensions:

### Dimension 1 — Target correctness (binary)

Does the step act on the service responsible for the fault?

- **Correct:** the step names the injected fault's target service.
- **Incorrect:** the step names a different service, or no service.

Scored against `expected_root_cause_service` in the cycle record.

### Dimension 2 — Action appropriateness (three tiers)

Is the action type a sensible response to this fault class?

- **Appropriate (2):** directly addresses the fault's mechanism.
- **Partial (1):** would relieve symptoms or incidentally clear the fault,
  but does not address the mechanism, or is a blunt instrument.
- **Inappropriate (0):** does not address the fault, or acts on an
  unrelated subsystem.

A plan is scored **fully correct** only when the target is correct AND the
action is Appropriate.

---

## Action taxonomy

Proposed steps are classified by verb and resource into:

| Class | Example |
| --- | --- |
| `SCALE_HORIZONTAL` | `kubectl scale deployment/X --replicas=N`, `kubectl autoscale` |
| `SCALE_VERTICAL` | `kubectl set resources deployment/X --limits=...` |
| `RESTART` | `kubectl rollout restart deployment/X`, `kubectl delete pod X` |
| `ROLLBACK` | `kubectl rollout undo deployment/X` |
| `CONFIG_PATCH` | `kubectl patch configmap/X` |
| `DIAGNOSTIC` | `kubectl get`, `describe`, `logs`, `top` |

---

## Per-scenario mapping

### S1 — CPU Stress (busy-loop injected into `cartservice`)

The mechanism is sustained CPU saturation within one container.

- **Appropriate:** `SCALE_VERTICAL` (raise the CPU limit the workload is
  saturating), `SCALE_HORIZONTAL` (distribute load across replicas)

  *On `SCALE_HORIZONTAL`:* adding replicas does not reduce CPU pressure on
  the already-saturated pod. It is scored Appropriate because it addresses
  the fault at the **service level** — restoring aggregate capacity and
  the service's ability to serve requests — not at the level of the
  individual affected pod. This distinction matters because the injected
  fault is a busy-loop simulating a runaway code path rather than genuine
  traffic load; against real load, horizontal scaling is the textbook
  response, whereas against a per-pod bug it relieves the SLO without
  fixing the pod. We score at the service level throughout, since that is
  the level at which an operator's remediation goal is defined.
- **Partial:** `RESTART` — recreating the pod does kill the injected
  process, so it incidentally resolves this fault, but an operator facing
  genuine CPU saturation from application load would see the problem
  return. Blunt, not wrong.
- **Partial:** `DIAGNOSTIC` — appropriate as a first move but resolves
  nothing.
- **Inappropriate:** `ROLLBACK` (no deployment change preceded the fault),
  `CONFIG_PATCH` (no configuration is implicated).

### S2 — Pod Crash (force-delete `cartservice`)

Kubernetes self-heals: the ReplicaSet recreates the pod without
intervention. The correct operational response is to verify recovery and
establish cause, not to act.

- **Appropriate:** `DIAGNOSTIC` — confirm the replacement pod is healthy
  and determine why the original terminated.
- **Partial:** `RESTART` — harmless but redundant; the pod has already
  been replaced.
- **Inappropriate:** `SCALE_HORIZONTAL`, `SCALE_VERTICAL` (a crash is not
  a capacity problem), `ROLLBACK`, `CONFIG_PATCH`.

Note: this scenario penalises over-action. An agent that always proposes
a remediation will score poorly here, which is the intended behaviour.

### S3 — Memory Pressure (Node.js heap allocation in `paymentservice`)

The mechanism is a growing heap within one process.

- **Appropriate:** `RESTART` (clears the heap — the standard first
  response to a suspected leak), `SCALE_VERTICAL` (raise the memory limit
  to buy headroom).

  *Why `RESTART` is Appropriate here but Partial in S1:* the tier tracks
  whether the action addresses the fault's **mechanism**, not the action
  class itself. A memory leak is accumulated process state, and restarting
  discards exactly that state — the action is aimed at the mechanism. CPU
  saturation is not accumulated state; restarting removes the current
  offending process, but nothing about the restart prevents the same load
  or code path from re-saturating the new pod. The same verb therefore
  earns different tiers across fault classes by design.
- **Partial:** `SCALE_HORIZONTAL` — spreads new traffic but the leaking
  pod continues to leak.
- **Partial:** `DIAGNOSTIC`.
- **Inappropriate:** `ROLLBACK`, `CONFIG_PATCH`.

### S4 — Network Latency (500 ms `tc netem` on `frontend`)

The mechanism is an injected delay on the pod's network interface. No
`kubectl` verb addresses `tc` rules directly.

- **Appropriate:** `RESTART` — pod recreation discards the interface and
  its queueing discipline, genuinely clearing the fault.

  *Assumption, stated explicitly:* this tier holds because the `tc netem`
  qdisc is applied to the pod's own network interface inside the pod
  network namespace (see `experiments/fault_injection/rerun_s4.ps1`).
  Deleting the pod destroys that interface along with the qdisc. Had the
  delay been injected at the node or CNI level, a pod restart would not
  clear it and `RESTART` would be Inappropriate. We flag this because
  "restart resolves network latency" is counterintuitive in general and is
  true here only as a consequence of the injection method. Readers
  evaluating the agent against production network faults should treat this
  tier as testbed-specific.
- **Partial:** `DIAGNOSTIC` — the correct investigative move for latency
  of unknown origin.
- **Inappropriate:** `SCALE_HORIZONTAL`, `SCALE_VERTICAL` (capacity is not
  the constraint; added replicas inherit nothing and fix nothing),
  `ROLLBACK`, `CONFIG_PATCH`.

### S5 — Cascading Failure (force-delete `redis-cart`)

The mechanism is an unavailable dependency. `cartservice` and `frontend`
surface the errors but are not at fault. This scenario is the sharpest
discriminator in the set: it separates symptom-treating from
cause-addressing.

- **Target correctness is the primary signal here.** A step targeting
  `redis-cart` is correct; a step targeting `cartservice`, `frontend` or
  any other service is incorrect regardless of action type.
- **Appropriate:** an action that restores or repairs `redis-cart`
  (recreate, restart, scale up from zero).
- **Partial:** `DIAGNOSTIC` against `redis-cart` — correct localisation,
  but inspection alone leaves the dependency down and the outage
  ongoing.
- **Inappropriate:** any action against `cartservice`, `frontend` or an
  unrelated subsystem — treating the symptom while the dependency stays
  down.

Note that this tiering deliberately withholds full credit for correct
diagnosis. Identifying the failed dependency is the harder cognitive
step and is captured by Dimension 1; Dimension 2 asks the separate
question of whether the agent then proposes to fix it.

### S6 — Noisy Baseline (no fault injected)

No remediation is warranted. Any proposed action is a false positive.

- **Appropriate:** no step proposed, or `DIAGNOSTIC` only.
- **Inappropriate:** any state-changing action.

---

## Handling plans with no LOW-risk step

A LOW-risk step is **expected** where the fault requires operator
intervention to resolve (S1, S3, S4, S5), and **not expected** where
Kubernetes self-heals (S2) or where no fault was injected (S6).

- Where expected, a confirmed anomaly with no LOW-risk step is scored as
  a miss: target incorrect, action tier 0.
- Where not expected, a confirmed anomaly with no LOW-risk step is scored
  as correct restraint: action tier 2, and Dimension 1 is not applied.

This follows the same principle as the over-action scoring above: the
agent is credited for matching its response to whether intervention is
warranted, not for always producing one.

---

## Reported metrics

Per scenario and pooled:

1. **Target accuracy** — proportion of scored plans naming the correct
   service.
2. **Action appropriateness** — mean tier score (0–2), and the proportion
   scoring Appropriate.
3. **Fully correct plan rate** — proportion with correct target AND
   Appropriate action. This is the headline figure.
4. **Over-action rate (S2, S6)** — proportion proposing a state-changing
   step where none was warranted.

---

## Known limitations of this rubric

- It measures whether a plan is *the kind of thing a competent operator
  would do*, not whether executing it would restore service. Only
  execution can establish that.
- Tier boundaries between Appropriate and Partial are judgment calls made
  in advance. They are published here so a reader can disagree with a
  specific assignment and recompute.
- Scoring only the first LOW-risk step ignores the rest of the plan. A
  plan whose second step is excellent scores no better for it, and a
  MEDIUM- or HIGH-risk step that is badly wrong never counts against the
  fully-correct-plan rate. This scoping is deliberate and matches the
  framework's autonomy boundary: only LOW-risk, high-confidence steps are
  surfaced for rapid operator confirmation (Section 4.6), so only those
  steps carry an implicit quality claim. Reported figures must therefore
  be read as the quality of the **surfaced** step, not of the whole plan.

- The Dimension 2 tier boundaries are judgment calls fixed in advance, and
  reasonable practitioners may disagree with specific assignments. We do
  not claim they are uniquely correct. Two mitigations: the complete
  mapping is published here so any disagreement can be localised to a
  named tier and the metrics recomputed from the public cycle records;
  and the mapping was informally reviewed prior to scoring by a
  practising DevOps engineer, whose comments led to the three rationale
  notes above (S1 service-level scoring, S3/S1 `RESTART` divergence, S4
  injection-layer assumption). This reviewer is a relative of the first
  author and took no part in the system's design or evaluation; we
  therefore report the review as a sanity check on the instrument's
  wording, not as independent validation, and claim no inter-rater
  agreement statistic.
- S6 has no injected target service, so Dimension 1 does not apply.
