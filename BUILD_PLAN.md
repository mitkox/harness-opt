# HOP — Harness Optimization Platform
## Coding-agent implementation plan

Version: 0.1 design handoff  
Prepared: 12 September 2026  
Status: implementation specification, not an implemented or benchmarked product

## 0. Mission and operating boundary

Build a local-only platform that develops, evaluates, optimizes, packages, deploys, observes, and retires configurations for enterprise software-development agents. Optimize the whole executable configuration, including skills and their executable resources, rather than only the system prompt.

Initial model targets are DeepSeek-V4-Flash, DeepSeek-V4.1-Flash, Qwen3.8-Flash-Next, Qwen3.8-27B, and GLM-5.3-Flash. These are discovery labels, not sufficient model identities. Each supported deployment must identify its exact local weights, quantization, tokenizer, chat template, serving runtime, and measured capabilities. Official model cards exist for these targets [S17–S21], but their hosted service settings and benchmark results must not be assumed to describe a particular local serving configuration.

Initial harness targets are Pi, Prime Agent, OpenCode v2 (the user's `opencode2` target), and DeepSeek Harness. Treat each pinned harness build as a separate integration. OpenCode's current v2 documentation calls its executable `opencode`; discover the installed executable and version instead of assuming that `opencode2` is its upstream binary name [S05].

The initial host is the user's maxed-out Strix Halo. Start with an already-operational Qwen deployment and a lightweight control plane. Expand to other owner-controlled local workers as capacity permits. Do not assume that every named checkpoint can run on the Strix Halo, or that all five can stay resident concurrently. An unavailable or oversized deployment is marked `not_available_locally` or `capacity_blocked`, never replaced with a cloud endpoint.

All model inference is local: task execution, routing, embeddings, summarization, judges, candidate generation, reflection, and synthetic test generation. All traces, patches, source snapshots, and evaluation records remain local. External Git providers are optional enterprise integrations, not inference dependencies. A strict disconnected mode uses only local Git and artifact mirrors.

The earlier conversational prompt styles, reasoning budgets, and score examples are hypotheses, not measured defaults. This project must discover compatibility and establish its own baselines before assigning preferred configurations.

## 1. Product contract

The platform should answer five questions with evidence:

1. Which approved model, harness, agent configuration, and skill set should handle this task on this hardware?
2. Did the resulting change or review satisfy independently checked requirements?
3. What happened during execution, including skill discovery, tool use, delegation, failures, and verification?
4. Which configuration changes improve held-out outcomes, and which skills should be rewritten, split, disabled, or retired?
5. Does a new model, quantization, server, harness, or repository dependency change invalidate an existing approval?

The deliverable is a reproducible **execution bundle**, not a folder of Markdown files. Its identity includes:

```
model deployment + harness build + base prompt
+ organization policy + project instructions + agent graph
+ selected skill variants + tool implementations
+ context and memory policies + inference configuration
+ compiler/APM locks + sandbox image + hardware envelope
```

A bundle may be optimal for one workflow and hardware envelope without being best elsewhere. Do not claim global optimality or assume that more iterations must improve quality.

Success means the system can prove an improvement, safely keep the incumbent when evidence is inconclusive, and roll back a regression. Discovering that a model performs better with fewer optional skills is a successful optimization result.

## 2. Non-negotiable invariants

**Local-only execution.** Network enforcement, not prompts, must restrict inference destinations. Disallow cloud fallbacks and external telemetry. Test all subprocesses and sidecars, including compaction, title generation, embedding, judge, and optimizer paths. A private-looking URL alone is not proof of locality; deployments must resolve to registered local endpoints, authenticate where appropriate, and be protected against redirects and DNS changes.

**Immutable production bundles.** A run is bound to a signed, content-addressed bundle at admission. A mutable channel such as `stable` resolves once. Never overwrite a shared skill directory while a run is using it. Record explicit child runs for model escalation or restarted attempts.

**Independent verification.** Agent narration, harness exit status, and tool process exit codes are not sufficient evidence of task success. A trusted verifier evaluates a frozen output snapshot in a separate environment.

**Protected policies.** Optimizers cannot weaken authorization, sandboxing, data access, evidence requirements, review approvals, or hidden test protection. Policy enforcement remains outside model-generated text.

**No evaluation leakage.** Candidate-generating processes cannot read sealed holdout repositories, expected patches, hidden tests, verifier implementations, or prior held-out transcripts. Distinct benchmark cases get isolated writable memory and filesystem state.

**Honest observability.** Missing events, incomplete artifacts, estimated token counts, and unsupported model capabilities are explicit. A skill being available or read does not prove that it caused an outcome.

**Controlled side effects.** Evaluation and shadow runs cannot publish comments, push branches, trigger real deployments, or scan unapproved targets. Live writes flow through a separate permissioned publisher.

**Trace-derived content is untrusted.** Repository text, CI logs, tool outputs, and trajectories can contain instructions or secrets. Optimizer access is read-only, scoped, redacted where required, and separated from promotion credentials.

## 3. Architecture and implementation stack

Implement a modular monolith first, with separate worker processes and strong trust boundaries. Do not start with a fleet of microservices, a custom model server, or a replacement agent loop.

Recommended implementation choices are Python for the control plane, schema validation, scheduling, evaluation, optimization, and command-line interface; TypeScript for harness plugins where the harness requires it. Use FastAPI, Pydantic, PostgreSQL, and a pinned Python dependency lock. Choose exact supported language and dependency versions during the compatibility spike rather than installing unpinned `latest` packages.

PostgreSQL holds registry metadata, job state, outcomes, approvals, and a transactional outbox. A content-addressed local filesystem holds large artifacts initially; an object-store interface permits an internal S3-compatible service later. Workers use durable leases, fencing tokens, heartbeats, cancellation, and idempotency. Do not claim exactly-once execution: side effects require reconciliation and idempotency keys.

The telemetry stack is OpenTelemetry Collector, Tempo, Prometheus, and Grafana, self-hosted. Tempo receives trace projections; PostgreSQL and the artifact store retain authoritative evaluation records. Grafana documents a Collector-to-Tempo pipeline [S15]. Pin the OpenTelemetry GenAI convention revision and map it through a compatibility module because that specification has its own evolving repository [S14].

```
CLI / internal UI / Git and CI integrations
                   |
              task admission
                   |
       policy + profile resolver + registry
                   |
          immutable execution bundle
                   |
       scheduler and local capacity leases
                   |
     isolated harness worker and workspace
                   |
      Pi / Prime / OpenCode v2 / DSH
                   |
   local inference endpoint + authorized tools
                   |
           outputs + durable events
              /               \
     trusted verifier       OTel projection
              |               |
      evaluation ledger   Tempo / Grafana
              |
       local candidate optimizer
              |
     development + validation evaluation
              |
       sealed promotion evaluation
              |
     approval -> signed release -> canary
              |
       active profiles and rollback
```

Suggested source layout:

```
agent-optimization/
  AGENTS.md
  pyproject.toml
  uv.lock
  toolchains.lock.json
  src/hop/
    api/ cli/ contracts/ registry/ resolver/ compiler/
    admission/ scheduler/ workers/ policy/ security/
    models/ inference/ harnesses/ tools/ scm/
    telemetry/ trajectories/ verification/ evaluation/
    optimization/ promotion/ deployment/ reporting/
  adapters/
    pi/ prime/ opencode_v2/ deepseek_harness/
  skills/
    <skill>/contract.yaml
    <skill>/canonical/SKILL.md
    <skill>/scripts/ resources/ tests/ variants/
  agents/ prompts/ policies/
  profiles/models/ profiles/harnesses/ profiles/workflows/
  benchmarks/development/ benchmarks/validation/
  tests/unit/ contract/ integration/ security/ e2e/
  deploy/compose/ deploy/kubernetes/ dashboards/
  docs/adr/ docs/runbooks/
```

The sealed holdout is a separate evaluator-owned store, not a readable directory in this repository or worker image.

## 4. Domain model and contracts

Define the following versioned contracts before implementing adapters. Validate them at API admission, worker admission, and artifact import.

**ModelDeployment.** Discovery label; family; source artifact provenance; weight manifest hashes; tokenizer and chat-template hashes; quantization recipe and format; server image/build; parser implementation; inference settings; context limits measured locally; capabilities and their test evidence; hardware and memory envelope; local endpoint reference; status. License metadata is recorded for organizational review, not treated as a legal conclusion.

**HarnessBuild.** Source repository/revision, binary and dependency hashes, adapter revision, protocol version, configuration schema, supported interception points, permission surfaces, restart/resume semantics, home/config isolation behavior, built-in prompts/skills, and telemetry limitations.

**SkillSpec.** Stable semantic purpose, selection description, positive and negative trigger cases, input/output contract, allowed tool capabilities, side effects, preconditions, reference dependencies, canonical instructions, scripts and resources, deterministic tests, semantic invariants, mutable optimization surfaces, and provenance.

**SkillVariant.** Parent skill revision, target compatibility predicates, instructions/resources/scripts hashes, selection metadata, dependencies, mutation history, and evaluation status. Compatibility is evidenced; family inheritance can seed a candidate but cannot approve it.

**AgentSpec.** Role, tool and data scopes, skill dependencies, delegation graph, subagent budgets, context policy, memory namespace, input/output contract, and terminal criteria. Delegation may not increase a parent's privileges.

**TaskSpec.** Workflow, source provenance, repository snapshot, build environment digest, public prompt, acceptance requirements, permissions, budgets, benchmark split, replay fixtures, data classification, and an opaque verifier reference. Hidden verifier content never enters the task payload delivered to the agent.

**ExecutionBundle.** Fully resolved dependencies and compiled files, effective prompt provenance, model/harness identities, policy IDs, selected skill variants, APM state, sandbox/tools, approved resource envelope, source map, and content digest. Promotion evidence and signatures refer to that digest.

**Run and TrajectoryEvent.** Logical task ID, run and attempt IDs, parent/child relationships, bundle digest, timestamps, source sequence numbers, trusted observer identity, trace/span links, artifact references, and completeness status.

**EvaluationResult.** Verifier build, test and fixture hashes, pass/fail/inconclusive/error results, workflow-specific measurements, unsupported/missing measurements, confidence intervals, contamination checks, and attribution limits.

**Candidate and Release.** Parent bundles, exact diff, candidate generator model/prompt, optimization dataset membership, mutation permissions, evaluation report, approvals, channel assignment, revocation and rollback target.

Suggested APIs to build, not existing product commands:

```
POST /v1/models/register          POST /v1/models/{id}/probe
POST /v1/tasks                   POST /v1/runs
GET  /v1/runs/{id}/events         POST /v1/runs/{id}/cancel
GET  /v1/runs/{id}/artifacts      POST /v1/evaluations
POST /v1/optimization-jobs       GET  /v1/candidates/{id}
POST /v1/releases/prepare        POST /v1/releases/{id}/approve
POST /v1/channels/{id}/promote    POST /v1/channels/{id}/rollback
GET  /v1/workers                 GET  /v1/compatibility
```

All mutations require scoped authorization and idempotency keys where retried. Task IDs, run IDs, and repository names are not authorization boundaries by themselves. Separate tenant/repository permissions must be checked.

## 5. Model identity and local serving compatibility

Register all five requested model labels as pending targets. Require a real locally available deployment to transition to tested status. Retain both DeepSeek versions as independently testable local deployments; a provider's API retirement does not retire locally held weights.

A model deployment fingerprint must cover weights, tokenizer, chat template, quantization, adapter/LoRA state where used, server build, tool-call/reasoning parsers, configured context length, positional overrides, KV-cache configuration, speculative decoder, and generation parameters. Record driver and device information in the tested runtime envelope.

Do not infer capability from the marketing model name or `/v1/models`. The server management layer must attest which artifacts are loaded. Probe serialized tool calls, parallel calls, tool-result association, long/malformed arguments, streaming boundaries, Unicode, cancellation, JSON output, stop handling, context overflow, and reasoning parameter support. Include multi-turn and tool-error recovery cases.

Probe requested-versus-effective inference settings. Reject unsupported parameters or record an explicit supported translation. Never silently send one family's `reasoning_effort`, thinking-preservation settings, or chat template to another. Model cards are starting documentation, not proof of local server behavior.

Implement local endpoint connectors for the user's active serving stacks behind a small interface. Prefer direct local API access initially; add a transparent local recording proxy only when required by missing harness hooks. Preserve streaming, cancellation, tool arguments, and backpressure. Validate proxy equivalence against direct calls. Keep unique, authenticated endpoint aliases and deny fallback outside the local allowlist.

Admission control measures resident model memory, peak prefill/KV demand, sandbox/tool memory, and telemetry/control-plane reserves. Protect interactive serving from experimental jobs. On the Strix Halo, use conservative concurrency and serialized heavyweight evaluations; sequence actor, judge, and optimizer phases rather than assuming concurrent model residency. Record queue delay separately from execution latency.

## 6. Harness adapters

Each adapter implements the same platform lifecycle while preserving native behavior:

```
probe() -> HarnessCapabilities
prepare(bundle, workspace, identity) -> PreparedSession
start(task, session) -> NativeRunHandle
stream_events(handle, cursor) -> normalized events
snapshot(handle) -> artifacts and resume metadata
cancel(handle) -> cancellation evidence
finalize(handle) -> outputs, terminal status, completeness report
```

Use official headless interfaces when available. Avoid terminal scraping as the authoritative data source. Adapter acceptance includes model selection, tools, streaming, errors, token accounting, cancellation, crash cleanup, isolated configuration, and artifact capture. Explicitly mark unsupported combinations.

### Pi

Start with Pi because its documented RPC mode provides JSON messages over stdin/stdout, streaming agent events, model selection, cancellation, and session inspection [S03]. Configure local deployments through explicit custom-model definitions [S04]. Pin the package/build and protocol schema. Capture native messages and supplement them with verified filesystem/tool events where necessary. Acceptance tests must distinguish prompt acceptance from task completion; the RPC acknowledgement is not a success grade.

### Prime Agent

Use Prime's own pinned RPC interface rather than assuming compatibility with the current Pi version [S10]. Capture REPL execution, Python package skill calls, recursion, subagent spawning/joining/messages, memory access, state revisions, and refinement deltas. Prime supports both Markdown and Python-backed skills [S09]. Optimize package code only under stronger executable-code review and tests.

Prime documents `/refine` as changing supplemental durable harness state while retaining its immutable base prompt [S08]. In evaluations, begin each independent task with clean writable state. In live sessions, keep approved state read-only and session learning in a separate overlay. Refinement output becomes a candidate diff with source trajectory links, not a new shared production default. Prime's Python execution is not a security sandbox [S08]; the external worker boundary must provide isolation.

### OpenCode v2

Use the v2 provider/configuration contracts and API rather than v1 examples [S05–S07]. Its provider documentation supports custom compatible endpoints; it also notes that vLLM discovery cannot infer tool support [S06]. Explicitly set capabilities only after local probes.

The documented global event stream is volatile and can miss events during disconnection. The session log endpoint is a separate, experimental durable interface with sequence-based continuation [S07]. Use a pinned durable session log where verified, plus worker-side persistence. Reconnect from the acknowledged cursor and deduplicate events. If completeness cannot be recovered, mark the run ineligible for promotion. Pin plugins and isolate watched configuration to prevent automatic reload from changing a running evaluation [S22].

### DeepSeek Harness

Implement against a pinned source release and official plugin interfaces. The project is explicitly a developer preview with model, tools, skills, storage, loop, scheduling, and sandbox capabilities provided through plugins [S11–S12]. First establish its headless interface and event contracts in an integration spike. Add a local model provider plugin and durable observation integration where needed; do not assume a cloud-oriented default automatically supports local endpoints.

Do not depend on undocumented preview internals without a maintained adapter boundary and contract tests. A DeepSeek model may run on any qualified harness; DeepSeek Harness may be evaluated with Qwen and GLM when its provider integration passes probes.

## 7. Skill compiler and APM distribution

APM remains a packaging and dependency-management component. Its documented lockfile and frozen installation behavior provide useful dependency pinning [S01–S02]. Do not make the platform's optimizer, model registry, task scheduler, or promotion authority depend on APM's internal implementation.

Compiler inputs are semantic skills, agent contracts, organization policy, project instructions, model compatibility, and harness capabilities. Compiler outputs are a content-addressed bundle, harness-native configuration, explicit enabled/disabled skill catalog, source map, and compatibility report.

Support deterministic transformations for file layout and tool bindings. Do not use an LLM during release compilation: LLM rewrites belong in candidate generation, followed by evaluation and an approved immutable source revision. The same input hashes must compile to identical output bytes.

Preserve skill purpose and behavioral invariants across variants. Optimize separately:

- Discovery: descriptions, trigger examples, namespace, catalog order, collision handling, and negative cases.
- Instructions: goal-oriented versus procedural versions, examples, verification guidance, length, and structure.
- Executable assets: helper scripts, imports, schemas, local package dependencies, reference freshness, and structured outputs.
- Composition: which skills are installed, exposed, loaded, or delegated; precedence, dependencies, redundancy, and conflicts.

Evaluate executable behavior, not only Markdown quality. A shorter description that selects the wrong skill is a regression. A skill script that runs faster but suppresses failures is a rejected mutation.

Use APM only for targets supported by the pinned version; independently qualify its OpenCode v2 output. Provide platform-owned exporters for Pi, Prime, and DeepSeek Harness wherever APM lacks a verified native target. Run packaging in an isolated build directory, never against an active developer workspace.

Promotion creates a signed release manifest binding bundle digest, lockfiles, all executable resources, model/harness compatibility, verifier versions, and evaluation evidence. Frozen APM installation is not proof of runtime model identity, behavioral quality, or a complete air-gapped environment. Mirror required Git and package artifacts locally and test disconnected replay.

Distribution is pull-based desired-state reconciliation. A node fetches an approved channel, verifies signatures/hashes, stages files, checks compatibility, and atomically activates them for new sessions. Drift produces a visible diff and quarantine or repair decision; it must not silently overwrite a developer's uncommitted work. Old bundles stay available for rollback and active sessions.

## 8. Enterprise workflow packs

Build six initial workflow packs with independent permissions, outputs, and verifiers.

| Pack | Typical tasks | Independent success evidence |
|---|---|---|
| Coding and refactoring | Features, API changes, migrations, behavior-preserving refactors | Hidden behavioral tests, regression checks, API compatibility, scope review |
| Debugging | Reproduce bugs, inspect logs, fix concurrency/data/config faults | Reproducer fails on baseline and passes on patch, negative tests, regression suite |
| PR/MR review | Correctness, maintainability, security, missing-test findings | Validated findings with exact file/line evidence, precision, recall on labeled cases, abstention on clean changes |
| CI and build engineering | Broken pipelines, packaging failures, dependency issues, flaky jobs | Pinned local pipeline reproduction, repaired intended stages, no removed checks, fixture provenance |
| Defensive security | SAST triage, dependency/IaC/container findings, authorization bugs, hardening | Reproducible authorized findings, safe repair, scanner/rule evidence, security and functional tests |
| Test and dependency maintenance | Test generation, migrations, upgrades, coverage gaps | Mutation/fault detection, edge-case behavior, compatibility, clean dependency/build output |

Start with languages represented by the actual pilot repositories. Use C#/.NET, Python, TypeScript, and Go as candidate initial tracks, not a claim about uninspected repositories. Add Java, C/C++, Rust, shell, and infrastructure templates as needed.

Coding runs emit a patch and completion evidence. Review runs emit structured findings rather than modifying the branch. CI runs work against recorded logs and recreated environments. Security tasks are restricted to authorized repositories and isolated lab targets, with network scanning and exploitation disabled by default.

For review benchmarks, include clean changes, severity calibration, plausible but false findings, stale line numbers, and duplicate findings. Do not optimize review quality by comment count. Maintain deduplicated issue identity and match findings against manually checked labels or reproducible tests.

For CI benchmarks, distinguish product failure, flaky test, infrastructure failure, toolchain mismatch, and missing fixture. Disabling tests, loosening quality gates, or hiding errors cannot count as repairing the pipeline.

For security tooling, use pluggable local scanners with pinned rules and database snapshots. Mirror vulnerability data through a controlled update process. Trivy documents air-gapped operation and the required local database handling [S16]. Record database freshness so an offline scanner's lack of findings is not misrepresented as current coverage.

Git integration should first read task/PR/MR metadata and materialize local snapshots. Add controlled write-back for review comments or draft branches only after approval. Support provider adapters for GitLab, GitHub, and Azure DevOps without embedding provider-specific behavior in the optimizer.

## 9. Benchmark task packages and evaluation integrity

Each case is a reproducible task package:

```
case/
  task.yaml
  prompt.md
  repository.manifest.json
  environment.lock.json
  public-fixtures/
  visible-tests/
  source-provenance.json
```

The trusted evaluator separately stores hidden tests, grader logic, and labels. Never mount them read-only into the agent container: read-only still leaks their content. Pin both baseline and candidate output snapshots, then validate the candidate in a fresh verifier environment. Stop agent writes before output collection to prevent time-of-check/time-of-use manipulation.

For patch tasks, baseline validation must show the bug/feature gap without unrelated build breakage. Verify on the patched baseline using protected tests and environment definitions. Candidate-provided tests may contribute evidence, but cannot be the sole verifier. Prevent malicious test configuration, import hooks, and startup scripts from replacing or bypassing trusted evaluation logic.

Create development, validation, and sealed promotion splits grouped by repository, issue ancestry, near-duplicate patches, and time. Randomly splitting related commits is not adequate. Keep a fresh holdout rotation and an access ledger: repeated exposure of holdout results can turn it into another tuning set.

Use deterministic verifiers first; human review and calibrated local judges cover subjective properties. A judge must run without side-effecting tools, treat evidence as untrusted data, and return structured rubric-based judgments. Blind candidate identity, vary presentation order for pairwise assessment, and sample disagreements for human adjudication. Never let a local LLM override a hard verifier failure.

Cross-model judges can reduce one source of dependence but do not guarantee correctness. Record judge model, prompt, temperature, input truncation, calibration results, and agreement rates. High-risk approvals need independent evidence and human review.

Classify outcomes as `pass`, `fail`, `inconclusive`, `infra_error`, `cancelled`, or `telemetry_incomplete`. Report all assigned cases and all attempts. Separate infrastructure exclusions using a predeclared rule; do not quietly remove hard failures from the denominator.

## 10. Evaluation experiment design

Every supported model/harness/workflow starts with these arms:

A. Native harness plus mandatory organization policy, necessary project instructions, and fixed tools; no optional platform skills.
B. Canonical skill set with automatic selection.
C. Current approved model-specific bundle.
D. Proposed candidate bundle.

For skill-level diagnosis, add no-catalog, catalog-exposed-but-body-not-loaded, forced-on, automatic-selection, and selective ablation arms. These separate selection errors, catalog overhead, instruction effects, and script effects. Built-in harness skills that cannot be disabled are recorded as fixed components of the baseline.

Measure marginal utility against the same baseline with one optional component removed. For a skill set S and skill s:

```
U(s | S) = outcome(S) - outcome(S without s)
```

This is conditional on the task distribution, model deployment, harness, and other skills. Test pairwise conflicts and complementarity among frequent co-activations before blaming one skill from a single failure trace.

Use paired cases and matched execution conditions. Pin tool/environment revisions and budgets. Randomize or interleave arm order to reduce thermal, queue, and cache confounding. Keep cold-start and warm-cache measurements separate. Report model load time, queue time, agent time, tool time, verifier time, and end-to-end time separately.

Use repeated stochastic runs where needed and record seeds when supported; a seed is not a guarantee of bitwise determinism. Bootstrap confidence intervals at the task/repository cluster level rather than treating repeated attempts on the same case as independent tasks. Pre-register a meaningful effect size, non-inferiority margin, and candidate selection budget. Use a sealed confirmatory stage to control winner's curse from searching many variants.

Do not use one weighted score as the only promotion rule. First require policy, locality, audit completeness, and verifier integrity. Then require workflow quality and important-slice non-inferiority. Finally compare the Pareto frontier of completion quality, review precision, latency, token use, memory, and resource cost.

A possible pilot policy is zero observed critical violations in mandatory tests, a predeclared 2 percentage-point quality non-inferiority margin, and a practical efficiency improvement for a quality-equivalent candidate. These are proposed policy parameters, not proven universal thresholds. Small pilots cannot establish tight confidence bounds or demonstrate zero real-world risk; an underpowered comparison stays inconclusive.

Useful metrics include verified completion rate, first-attempt completion, intervention rate, regression rate, review precision/recall, reproducible security findings, false positives, valid tool-call ratio, repeated failed calls, speculative edits reverted, diff scope, compaction loss, tokens per verified task, p50/p95 runtime, peak memory, and energy where a trusted meter supports attribution.

Compare cross-model token counts cautiously: tokenizers differ. Use wall-clock/resource measures and completed work as primary cross-model efficiency metrics. Record whether energy is host-level or attributable to a run and avoid attributing a shared meter to one concurrent task without a defensible method.

## 11. Trajectory and trace observability

A trajectory is the ordered, durable record of an episode. A trace is a timing and causal visualization across services. Store both; neither replaces the other.

### Durable event ledger

Emit run admission, configuration resolution, policy decisions, model calls, streamed responses where retained, tool requests/results, skill catalog exposure, selection, loading, script execution, context changes, memory operations, subagent relations, patch artifacts, verifier calls, human interventions, and terminal outcomes.

Each event contains schema version, globally unique event ID, run/attempt/agent identifiers, source identity and sequence, observed and emitted timestamps, optional parent event IDs, trace/span IDs, bundle digest, event type, artifact references, data classification, and observer provenance. Keep model-reported claims separate from trusted observer facts.

Use per-source monotonic sequences and a durable ingest cursor; a distributed system does not have a natural perfect total order. Deduplicate at-least-once delivery. Link parent and child agents explicitly. Use local disk spool and transactional ingestion acknowledgements. Missing ranges create a completeness error until recovered. Raw content must not enter ordinary metric labels.

### Trace structure

```
workflow.run
  admission.resolve
  workspace.prepare
  agent.execute
    skill.catalog
    skill.select
    skill.load
    inference.request
      inference.prefill
      inference.decode
    tool.execute
    context.compact
    memory.retrieve
    subagent.spawn / child agent.execute / subagent.join
  output.freeze
  verifier.execute
  evaluation.record
  artifact.publish
```

Some spans such as prefill/decode require actual server instrumentation; do not fabricate them from client timestamps. Record client/server correlation IDs and distinguish measured, inferred, and unavailable values. Long sessions may use linked per-turn traces plus a run-level record to avoid oversized traces.

Record supported GenAI semantic attributes through a pinned mapper; use an `aop.*` namespace for platform-specific information (retained as the stable span namespace after the AOP→HOP rename; see ADR-008). Examples include bundle digest, model deployment ID, harness revision, skill ID and revision, workflow, experiment arm, verifier version, tool implementation hash, and event completeness. Store run IDs/digests on traces and indexed records, not unbounded Prometheus label dimensions.

### Redaction and retention

Keep full evaluation trajectories subject to explicit access and retention policy, with 100% event capture for promotion-eligible evaluation. Sampling operational traces is acceptable; losing authoritative evaluation evidence is not. Persist redacted artifacts before publishing telemetry. Restricted raw content, where allowed, belongs in an encrypted artifact store with separate permissions and retention, not in metric labels or exported span attributes.

Redaction may reduce replayability. Record which fields were redacted and whether a run is fully re-executable, partially reproducible, or view-only. Exposed model reasoning is optional sensitive content, not a required observation or proof of internal cognition. Do not depend on hidden chain-of-thought availability.

An observer outage causes durable buffering. A run with an unrecoverable ledger gap cannot support promotion. If policy requires complete audit and local buffering is exhausted, pause or stop execution rather than continuing silently. Test this explicitly.

### Investigation UI

Provide run search by workflow/model/harness/bundle and a trajectory viewer with source-linked skill events, code diffs, tool output, subagent tree, verifier evidence, and resource timeline. Provide paired-run comparison, skill activation confusion matrices, candidate ancestry, and promotion rationale. A Grafana trace view links back to the run's immutable artifacts.

Distinguish three operations: viewing a recorded trajectory, re-executing a task with a model, and re-evaluating a frozen patch. Recorded downstream tool outputs cannot be reused as valid counterfactual evidence when a candidate would have taken different actions.

## 12. Optimization engine

Optimize configuration first, not model weights. Fine-tuning is a later, separately governed model-onboarding path.

Allowed initial mutations are skill description/body/example changes, selective skill inclusion, reference cleanup, verified helper-script changes, system-prompt supplements, agent-role definitions, permitted delegation settings, tool descriptions, context packaging, compaction policies, and locally supported generation parameters. Never optimize by changing security policy, hidden tests, required gates, grading weights after seeing results, or network authorization.

Use staged search to avoid the full model × harness × skill × prompt × parameter Cartesian product:

1. Establish a working serving and harness baseline.
2. Identify workflow failure clusters with deterministic analysis and a local reflector.
3. Test deletion/ablation and small targeted edits before large rewrites.
4. Optimize discovery metadata separately from execution content.
5. Evaluate candidate instructions/resources against the unchanged semantic contract.
6. Tune supported runtime settings for the surviving candidates.
7. Test common skill combinations and permitted agent graphs.
8. Run held-out confirmation and capacity-matched operational tests.

GEPA is a suitable optional search backend because its documented interface supports trace-informed reflective optimization of text parameters and adapter-based evaluation [S13]. Keep it behind `OptimizerBackend`; the platform owns execution, scoring, permissions, datasets, and promotion. Explicitly configure all reflection and evaluation calls to local deployments rather than copying hosted-model defaults from examples.

Candidate generation receives redacted development failure records, allowed source files, and an explicit mutation schema. It writes a candidate branch or artifact diff, not the live bundle. Record the proposer model deployment, prompt, seed, parent candidate, source cases, changed fields, and compute budget. Candidate-generating jobs cannot access release signing keys.

Reject invalid candidates before model runs: schema errors, broken dependencies, executable test failures, forbidden file changes, policy changes, embedded credentials, prompt/catalog over-budget, missing provenance, or unsafe imports. Skill scripts are untrusted candidate code and run only in the external sandbox.

Use bounded mutation rounds, successive-halving or another documented budget policy, then confirm finalists without continuing to optimize against the sealed set. Maintain per-workflow champions rather than one global champion. A deletion can be a candidate. A statistically inconclusive candidate does not displace stable.

## 13. Dynamic operation: three separate feedback loops

### Per-task selection

At admission, choose among approved compatible bundles using workflow, repository profile, task difficulty features, context requirements, hardware capacity, privacy/risk policy, and queue budget. Begin with an explainable rules table. Later evaluate a learned local router against that baseline.

Do not confuse routing with optimization. Selecting a tested bundle is a production action; discovering a new bundle is an experiment. Log the choice and eligible alternatives. Use a deterministic approved fallback or pause when no compatible profile is available.

### In-run adaptation

Permit approved bounded actions: retry within budget, select an allowed skill, compact context using the pinned policy, or delegate to an approved child profile. Record every change. Model switching should normally create a child or restarted attempt with an explicit state handoff, not silently mutate the identity of the parent run. Reuse only validated context rather than copying incompatible raw reasoning/tool state between model families.

Production runs may produce session-local memory, but not rewrite shared skills or policies. Long-horizon adaptation experiments may update a sandbox-local overlay; label these as adaptive experiments and evaluate the entire adaptation policy from a fresh state per case.

### Continuous improvement

Triggers include approved production feedback, failure/incident clusters, repeated manual corrections, skill/runtime dependency changes, harness changes, new model artifacts, drift alarms, and a budgeted maintenance schedule. Human acceptance is useful but not a substitute for verified correctness.

Pipeline: collect -> scrub -> provenance check -> deduplicate -> curate cases -> assign splits -> generate candidate -> validate -> confirm -> approve -> canary -> promote -> monitor -> retire/rollback.

Use safe exploration only in isolated offline or shadow runs initially. Shadow runs execute on snapshots with side effects disabled. Production bandit experiments are a later opt-in feature limited to already approved profiles and low-risk tasks, with recorded assignment probabilities and human intervention controls.

## 14. New-model and infrastructure-change lifecycle

Use this state machine:

```
discovered -> staged -> provenance_checked -> capacity_checked
 -> protocol_qualified -> baseline_measured -> candidate_optimized
 -> promotion_evaluated -> shadow -> canary -> approved
 -> deprecated -> retired
```

At any stage a candidate may be `blocked`, `inconclusive`, or `quarantined` with a reason and remediation evidence.

Stage weights through a controlled local import. Verify manifests, source provenance, license metadata, format, and any required custom loading code. Do not permit arbitrary remote-code execution in the control plane. Record custom loading code as a reviewed, pinned runtime dependency.

Measure capacity and protocol compatibility before importing historical skills. Run the no-optional-skill baseline on each qualified harness. Re-evaluate the canonical skills, the prior family's winning configuration as a candidate, and targeted new variants. Family similarity is not evidence that old instructions remain useful.

Approve only tested workflow × harness × runtime-envelope combinations. Partial qualification is acceptable and must be visible. Build a dependency graph so a tokenizer/template/parser/server/quantization/toolchain change invalidates only the affected approvals, with broader regression runs for shared policy/compiler changes.

Canary and rollback use immutable bundle digests. Prefer a small supervised cohort or designated repository set before broad deployment. Do not determine success only from live merge rate; use delayed regressions, verified task outcomes, and intervention signals. Keep the prior model artifacts available until rollback retention expires.

## 15. Security and isolation architecture

Separate the control plane, untrusted agent workers, trusted verifier workers, and release publisher by identity, filesystem, network, and credentials. A registry signature prevents undetected artifact replacement but does not make an artifact safe to execute.

Use rootless containers for approved ordinary code workloads where the threat model permits, and VM-backed or Kata-class isolation for untrusted repositories/security fixtures after validating hardware support. Never mount host SSH agents, broad Git credentials, the container runtime socket, release keys, or hidden test storage into an agent worker. Nested untrusted tests still require containment.

Mount promoted bundles read-only; provide per-run writable workspace, home, cache, session, memory, and temporary directories. Namespace retrieval indexes and embeddings by tenant/repository and benchmark split. Shared read-only package caches are allowed only if data provenance and contamination controls are explicit.

Apply egress policy at the host/network boundary. Permit only registered inference, internal package mirrors, approved tool services, and approved Git connectors for the given mode. Test hostname redirects, proxy environment variables, IPv6, DNS, child processes, and tool downloads. Disable unattended harness/package updates and session-sharing integrations.

Code execution and shell commands need hard time, process, disk, memory, and output limits. Quota limits apply across all descendants. A local model can still issue destructive commands; model provenance does not replace sandboxing or authorization.

A separate write broker receives proposed branch pushes, comments, and CI actions. It verifies task authorization and approved intent, applies idempotency, and never blindly executes text from the model. Security remediation requires review before merging or deploying. Defensive scanning scope is explicitly declared; no autonomous probing of public targets.

Adversarial tests include malicious repository instructions, forged test logs, fake tool-call JSON, secret disclosure requests, unauthorized paths, hidden-test access, policy edits, malicious package lifecycle scripts, worker escape attempts in controlled fixtures, and observer outage. These tests are defensive and run within authorized lab environments.

## 16. Reliability, recovery, and resource fairness

Define task and run state machines with transition guards. Leases carry fencing tokens so a replaced worker cannot finalize or publish an old attempt. Cancellation propagates to model requests, child agents, shell processes, and the sandbox. Orphan processes are reconciled on startup.

Use transactional state-plus-outbox writes for changes that trigger jobs. Content artifacts are staged, hashed, and atomically finalized before records reference them as complete. Keep recoverable partial artifacts labeled incomplete. Publishing and comment writes need deduplication keys and provider-side reconciliation where possible.

Worker restarts should recover durable ingestion cursors and resume only when the harness explicitly supports safe continuation. Otherwise create a new attempt on a clean snapshot, retaining the failed attempt. Never silently append a new model run to the old run's timing metrics.

Back up PostgreSQL, bundle artifacts, benchmark manifests, local model manifests, and signing trust metadata. Test restore and a frozen offline evaluation. A backup that restores only the database but not its referenced content is not adequate.

For the local workstation, enforce separate resource pools for interactive serving, benchmarks, verification, and optimization. Use model-residency-aware scheduling and avoid overlapping heavyweight actor/judge/reflection processes. Measure instrumentation overhead and expose it; do not claim an arbitrary low overhead target without measurements.

## 17. Release and promotion gates

A candidate release needs all of the following evidence:

- All required artifact hashes and pinned dependencies resolve locally; no unresolved placeholders.
- Required model/harness/tool capability probes pass for the declared envelope.
- No disallowed network access or policy bypass in the mandatory suite.
- Trusted verifier and dataset integrity checks pass; sealed data was not exposed.
- Promotion-evaluation events and required artifacts are complete, or the candidate is rejected/inconclusive.
- Quality improvement or predeclared non-inferiority is established with appropriate uncertainty and important-slice checks.
- Efficiency measurements use matched cache/load/concurrency conditions and complete attempt accounting.
- Executable skill changes and security-sensitive permissions receive human review.
- Canary/rollback procedure is tested, and the incumbent bundle is available.

Release reports must show the population, number of tasks/repositories/attempts, exclusions, effect estimates, confidence bounds, compute budget, practical limitations, and candidate ancestry. Do not report development-set wins as production-level proof.

## 18. Phased implementation sequence

These are dependency-ordered milestones, not calendar estimates. Detailed work items are in `BACKLOG.yaml`.

### M0 — Contracts, threat model, and compatibility discovery

Create the repository, pinned toolchain, schemas, architecture decisions, trust-boundary model, local endpoint registry, and integration matrix. Record actual source/build identities for each harness and available model deployment. Produce findings instead of guessing unsupported interfaces.

Exit: the CLI validates a draft target profile, refuses unresolved release input, and identifies a real local Qwen/Pi pair; the threat model covers worker, verifier, telemetry, and publisher boundaries.

### M1 — Minimal vertical slice

Implement one workflow using Pi, one local model, a sandboxed workspace, immutable run manifest, durable events, patch collection, and independent verification. Use a small set of controlled debugging cases with known failing baselines and clean holdouts. Export a report and trace.

Exit: a task can pass, fail, timeout, be cancelled, and survive an observer reconnect without losing its recorded outcome. Task success derives from the trusted verifier, not agent text.

### M2 — Trace and trajectory evaluation

Complete event schema, spool/recovery, source maps, redaction, resource accounting, subagent links, Grafana integration, trajectory view, paired comparison, and failure taxonomy.

Exit: every promotion-eligible run has linked model/tool/skill/verifier evidence; injected missing events prevent eligibility. A secrets fixture does not leak into ordinary logs, spans, metrics, or reports.

### M3 — Canonical skills, compiler, and APM

Implement SkillSpec, variant generation inputs, deterministic compilation, native exporters, isolated APM packaging, lock verification, source maps, signed bundles, and atomic local activation.

Exit: identical inputs generate identical bundles; changing a script or reference changes identity; concurrent model profiles do not overwrite one another; a tampered bundle is refused.

### M4 — Enterprise benchmark packs

Add the six workflow packs, task snapshot imports, protected evaluator storage, development/validation/holdout partitions, deterministic verifiers, local judge calibration, and controlled reporting.

Exit: representative coding/debug/CI/test tasks have reproducible verification; clean reviews are rewarded for justified abstention; security and CI cases cannot pass by suppressing checks.

### M5 — All harness integrations

Qualify Prime, OpenCode v2, and DeepSeek Harness through the shared adapter suite. Handle Prime state/refinement and OpenCode durable event recovery explicitly. Add subagent accounting and permission inheritance.

Exit: each adapter runs the same smoke tasks or produces a specific supported/blocked capability report. A supported adapter cannot leak inherited global credentials, skills, memory, or external endpoints.

### M6 — Model matrix and fair benchmarking

Register all locally available requested checkpoints, probe capabilities, fingerprint serving state, implement capacity-aware scheduling, and compare qualified model/harness pairs under matched conditions.

Exit: labels never resolve silently to different deployments; a changed quantization or template invalidates affected approvals; cold/warm and queue/execution measurements remain distinct.

### M7 — First optimization loop

Implement ablations, bounded local candidate generation, static/script tests, development evaluation, validation selection, Pareto analysis, and a pluggable GEPA backend. Add source-provenance and leakage prevention.

Exit: the platform can produce a candidate, reject an invalid mutation, compare it with no-optional-skills/canonical/incumbent baselines, and generate a truthful improve/retain/inconclusive decision.

### M8 — Promotion, deployment, and rollback

Implement sealed confirmation, approvals, signing, desired-state channels, atomic activation, drift detection, shadow runs, supervised canary, and rollback. Add enterprise read/write brokers with fine-grained scopes.

Exit: a deliberately regressed bundle cannot promote; a signed but incompatible bundle is refused; stable can roll back without rewriting active sessions or duplicating external writes.

### M9 — Continuous lifecycle and new-model onboarding

Add local artifact discovery/import events, dependency invalidation, failure clustering, feedback curation, automatic candidate jobs, retirement, budget management, and notifications. Evaluate a rules-based router before any adaptive router.

Exit: importing a changed model artifact creates a new qualified baseline and candidate process rather than inheriting approval; a live failure can become a curated development case without exposing sealed data.

### M10 — Enterprise hardening and operational readiness

Complete tenant/repository authorization, retention, audit, restore testing, load fairness, disaster recovery, malicious-input suites, offline deployment manifests, runbooks, and measured instrumentation overhead.

Exit: reproduce a release evaluation from restored local artifacts with external network denied, exercise cancellation and rollback, and demonstrate authorization boundaries across repositories.

## 19. Coding-agent work protocol

Use the supplied `AGENTS.md` as the build-agent operating contract. Work in small reviewed changes with tests and explicit milestone exits. Parallelize only independent modules after schemas and protocol contracts are fixed.

A practical split is a lead integration agent, an execution/adapters agent, a telemetry agent, an evaluation agent, an optimization/compiler agent, and an independent security/review agent. They share documented contracts, not mutable working trees. Use separate worktrees and cap parallel activity to protect local inference capacity.

For every work item, the agent must inspect the relevant pinned upstream contract, implement the smallest vertical change, add unit and adversarial/contract tests, run available validations, record exact results, update the runbook/ADR when behavior changes, and leave a reviewable patch. Do not mark unavailable model tests as passing. Do not merge their own security-sensitive changes without independent review.

Initial coding-agent instruction:

> Implement M0 and M1 only. Establish the contracts, external sandbox boundary, local inference adapter, Pi adapter, durable run ledger, and one independently verified debugging workflow. Do not start prompt optimization, distributed orchestration, or automatic promotion until a clean end-to-end run is reproducible and telemetry completeness is testable. Treat all `hop` commands in this plan as interfaces to implement (`aop` remains only as a deprecated alias).

## 20. Definition of done for the complete platform

The platform is complete when all four harness adapters are implemented and qualified against available local targets or explicitly blocked with evidence; all five requested model labels have lifecycle entries; each of the six enterprise workflows has reproducible evaluation tasks and verifiers; every promotion-eligible run has attributable skill/model/tool/verifier evidence; and a full candidate-to-retirement lifecycle works without hosted inference.

It must demonstrate skill selection optimization, instruction optimization, executable skill validation, skill-set ablation, model-specific bundles, new-model requalification, safe session learning, controlled production feedback, dynamic selection among approved bundles, signed distribution, rollback, and offline recovery.

A release demonstration should include: one successful repair; one correctly rejected bad patch; one clean PR with no invented findings; one false security finding rejected by evidence; one CI attempt prevented from disabling checks; one incomplete-telemetry run blocked from promotion; one prompt-injected repository kept within permissions; one neutral/harmful skill removal experiment; and one model/quantization change requiring re-evaluation. Skill benefit is measured, not assumed: the demonstration may legitimately retain the incumbent when no improvement is established.

## 21. Evidence and current upstream references

The architecture, thresholds, backlog, contract names, and `hop` interface are design proposals. No enterprise benchmark was executed while preparing this plan. Sources below establish current upstream capabilities; integration tests must pin the precise versions actually used.

S01. APM install and frozen replay: `https://microsoft.github.io/apm/reference/cli/install/`

S02. APM lockfile specification: `https://microsoft.github.io/apm/reference/lockfile-spec/`

S03. Pi RPC protocol: `https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md`

S04. Pi custom models: `https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md`

S05. OpenCode v2 introduction and executable naming: `https://opencode.ai/v2/docs`

S06. OpenCode v2 providers and local endpoint discovery: `https://opencode.ai/v2/docs/providers/`

S07. OpenCode v2 API, session log, and volatile global events: `https://opencode.ai/v2/docs/api`

S08. Prime Agent architecture, refinement, and security boundary: `https://github.com/PrimeIntellect-ai/prime-agent`

S09. Prime Markdown and Python-backed skills: `https://github.com/PrimeIntellect-ai/prime-agent/blob/main/packages/coding-agent/docs/skills.md`

S10. Prime RPC protocol: `https://github.com/PrimeIntellect-ai/prime-agent/blob/main/packages/coding-agent/docs/rpc.md`

S11. DeepSeek Harness developer preview: `https://www.deepseek.com/harness/en/`

S12. DeepSeek Harness source: `https://github.com/deepseek-ai/deepseek-harness`

S13. GEPA reflective optimization and adapters: `https://github.com/gepa-ai/gepa`

S14. OpenTelemetry GenAI semantic conventions: `https://github.com/open-telemetry/semantic-conventions-genai`

S15. OpenTelemetry Collector with Tempo: `https://grafana.com/docs/tempo/latest/set-up-for-tracing/instrument-send/set-up-collector/otel-collector/`

S16. Trivy disconnected operation: `https://trivy.dev/docs/latest/advanced/air-gap/`

S17. DeepSeek-V4-Flash model card: `https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash`

S18. DeepSeek-V4.1-Flash model card: `https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash`

S19. Qwen3.8-Flash-Next model card: `https://huggingface.co/Qwen/Qwen3.8-Flash-Next`

S20. Qwen3.8-27B model card: `https://huggingface.co/Qwen/Qwen3.8-27B`

S21. GLM-5.3-Flash model card: `https://huggingface.co/zai-org/GLM-5.3-Flash`

S22. OpenCode v2 plugin loading and updates: `https://opencode.ai/v2/docs/plugins/`
