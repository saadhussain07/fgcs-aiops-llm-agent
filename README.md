# A Telemetry-Driven LLM-Agent Framework for Fault Detection and Advisory Remediation in Cloud-Native Kubernetes Systems

Source code, deployment manifests, and complete experimental data for the paper of the same name.

The framework ingests Prometheus metrics, OpenTelemetry traces, and Loki logs, fuses them into a single structured context object, and passes that object to an LLM agent that classifies anomalies, localises root causes, and proposes confidence-gated, risk-stratified `kubectl` remediation steps. **No remediation step executes without explicit operator confirmation.**

---

## Table of contents

- [Key results](#key-results)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Setup](#setup)
- [Running the pipeline](#running-the-pipeline)
- [Reproducing the reported results](#reproducing-the-reported-results)
- [Data format](#data-format)
- [Citation](#citation)
- [Licence](#licence)

---

## Key results

Evaluated on a live two-node Kind cluster across six scenarios, five independent runs each (330 total monitoring cycles):

| Metric | Value |
| --- | --- |
| Pooled binary detection F1 | 0.965 |
| False-positive rate (fault-free control, S6) | 0.0% |
| Non-LLM baseline F1 (logistic regression, identical features) | 0.966 ± 0.030 |
| Root-cause accuracy (joint service + fault class) | 88% (22/25) |
| Root-cause accuracy (service only) | 96% (24/25) |
| Confidence calibration | *r* = 0.47, *p* < 0.0001, Brier = 0.046 |

The LLM agent and a conventional classifier reach **statistically comparable detection performance**. This is a deliberate finding rather than a shortfall: it locates most raw detection accuracy in the engineered context-fusion pipeline, not in LLM reasoning. The agent's distinguishing contribution is root-cause attribution and risk-stratified remediation, neither of which a feature-based classifier can produce.

---

## Repository layout

```
.
├── src/                         Telemetry pipeline and LLM agent
│   ├── metric_collector.py        Prometheus queries + Isolation Forest scoring
│   ├── trace_analyser.py          Jaeger p99 latency and error-span analysis
│   ├── log_parser.py              Loki queries + Drain3 template mining
│   ├── context_builder.py         Multi-signal context-object fusion
│   ├── runtime_anomaly_agent.py   LLM agent, 5-way self-consistency voting
│   └── main.py                    Pipeline orchestration loop
│
├── experiments/
│   └── fault_injection/         Per-scenario injection scripts (S1–S6)
│
├── data/                        Raw experimental records, one file per scenario
│   ├── S1_CPU_STRESS.jsonl
│   ├── S2_POD_CRASH.jsonl
│   ├── S3_MEMORY_PRESSURE.jsonl
│   ├── S4_NETWORK_LATENCY.jsonl
│   ├── S5_CASCADING_FAILURE.jsonl
│   └── S6_NOISY_BASELINE.jsonl
│
├── deploy/                      Cluster and observability stack manifests
│   ├── microservices-lite.yaml    Six-service workload
│   ├── loadgenerator.yaml
│   ├── prometheus-values.yaml
│   ├── loki-values.yaml
│   └── jaeger.yaml
│
├── requirements.txt
├── .env.example
├── LICENSE                      MIT — applies to all source code
└── LICENSE-DATA                 CC BY 4.0 — applies to data/
```

---

## Requirements

- Python 3.13
- Docker Desktop with at least 6 GB allocated
- [Kind](https://kind.sigs.k8s.io/) (cluster tested on Kubernetes v1.32.2)
- `kubectl` and Helm
- A [Groq API key](https://console.groq.com/keys)

---

## Setup

**1. Clone and install dependencies**

```bash
git clone https://github.com/Muhib78600/<REPO-NAME>.git
cd <REPO-NAME>
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure credentials**

```bash
cp .env.example .env            # Windows: copy .env.example .env
```

Edit `.env` and insert your Groq API key. This file is gitignored and must never be committed.

**3. Create the cluster and deploy the observability stack**

```bash
kind create cluster --name new-cluster --config deploy/kind-config.yaml

helm install prometheus prometheus-community/kube-prometheus-stack \
  --version 65.x -f deploy/prometheus-values.yaml
helm install loki grafana/loki-stack \
  --version 2.10 -f deploy/loki-values.yaml
kubectl apply -f deploy/jaeger.yaml
```

**4. Deploy the application workload**

```bash
kubectl apply -f deploy/microservices-lite.yaml
kubectl apply -f deploy/loadgenerator.yaml
kubectl wait --for=condition=ready pod --all --timeout=300s
```

**5. Bridge the telemetry backends to localhost**

```bash
kubectl port-forward svc/prometheus-operated 9090:9090 &
kubectl port-forward svc/loki 3100:3100 &
kubectl port-forward svc/jaeger-query 16686:16686 &
```

---

## Running the pipeline

```bash
python src/main.py
```

Each monitoring cycle queries all three telemetry backends, fuses the results into a structured context object, and — if any anomaly is flagged — invokes the Runtime Anomaly Agent. The agent queries the LLM five times at temperature 0.1 and aggregates by majority vote on the verdict and mean confidence.

A remediation step is queued for rapid operator confirmation only when the verdict is confirmed, mean confidence ≥ 0.85, and the step is tagged LOW risk. Medium- and high-risk steps escalate to a human regardless of confidence. **Nothing executes automatically.**

---

## Reproducing the reported results

Fault-injection scripts for each scenario are in `experiments/fault_injection/`. Each script injects its fault, runs the monitoring pipeline for the configured number of cycles, and appends records to the corresponding file in `data/`.

| Scenario | Fault | Injection | Runs × cycles |
| --- | --- | --- | --- |
| S1 | CPU stress | Busy-loop via `kubectl exec` into `cartservice` | 5 × 12 |
| S2 | Pod crash | Force-delete `cartservice` | 5 × 12 |
| S3 | Memory pressure | Node.js heap allocation in `paymentservice` | 5 × 6 |
| S4 | Network latency | 500 ms delay via `tc netem` on `frontend` | 5 × 12 |
| S5 | Cascading failure | Force-delete `redis-cart` | 5 × 12 |
| S6 | Noisy baseline | None (fault-free control) | 5 × 12 |

Between runs the cluster is restored to a verified baseline — two consecutive stable Prometheus cycles — and per-service statistical baselines are refit.

S3 uses six cycles per run rather than twelve because memory readings are point-in-time queries rather than windowed averages, so shorter runs do not dilute the signal the way CPU's trailing-window average would.

---

## Data format

Each line in a `data/*.jsonl` file is one complete monitoring cycle. Records carry `run_id` and `cycle_num`, so per-run statistics are fully recoverable from the raw files.

| Field | Meaning |
| --- | --- |
| `run_id`, `cycle_num` | Run index (1–5) and cycle index within that run |
| `expected_root_cause_service` | Injected ground-truth service, for RCA scoring |
| `expected_fault_class` | Injected ground-truth fault class |
| `context_object` | The fused multi-signal context passed to the LLM |
| `context_object.full_metrics` | Per-service metrics and Isolation Forest scores |
| `context_object.full_logs` | Per-service log counts and Drain3 templates |
| `context_object.summary_for_llm` | Natural-language summary injected into the prompt |
| `llm_verdict` | The agent's aggregated structured output |
| `llm_verdict.vote_agreement` | Self-consistency agreement, e.g. `"5/5"` |
| `llm_verdict.all_votes` | The individual verdicts from all five votes |
| `pipeline_status` | **What the pipeline actually did with the verdict** |

### Two fields that are easy to misread

**`llm_verdict.auto_remediate` is a model output, not an execution record.** It is the LLM's own suggestion flag, captured verbatim for transparency and analysis. It does not gate anything. The authoritative record of what the pipeline did is `pipeline_status`, which takes values such as `ESCALATED_TO_HUMAN`, `QUEUED_FOR_CONFIRMATION`, or `MONITORING`. No cycle in this dataset resulted in autonomous execution, because the prototype contains no autonomous execution path.

**`llm_verdict.confidence` is the mean across the five votes**, not a single sample. A cycle can therefore show `ANOMALY_CONFIRMED` with confidence below the 0.85 gate — in which case it escalates to a human rather than being queued for confirmation. Cycles of exactly this kind are present in the data and are the confidence gate working as designed.

---

## Citation

```bibtex
@article{hussain2026telemetry,
  title   = {A Telemetry-Driven {LLM}-Agent Framework for Fault Detection
             and Advisory Remediation in Cloud-Native {K}ubernetes Systems},
  author  = {Hussain, Muhammad Saad and Farrukh, Alishba},
  year    = {2026},
  note    = {Under review}
}
```

---

## Licence

Source code is released under the MIT Licence (`LICENSE`). The experimental data in `data/` is released under CC BY 4.0 (`LICENSE-DATA`). If you use either, please cite the paper above.