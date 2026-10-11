# agentbench — Agent Reliability & Evaluation Harness

A framework that runs LLM agents on tool-use tasks, **injects failures** (tool errors, timeouts,
malformed outputs, silent data corruption, prompt injection), **logs structured traces**, **scores
robustness**, and publishes a **failure taxonomy with numbers**. Stdlib-only Python; one command
reproduces everything.

```bash
python3 -m unittest discover -s tests     # 15 tests
python3 -m agentbench run --seeds 10      # 3 designs x 9 conditions x 25 tasks x 10 seeds = 6,750 runs, ~seconds
python3 -m agentbench sweep               # sensitivity of the ranking to model susceptibility
python3 -m agentbench run --backend gemini --max-tasks 8 --seeds 1   # live Gemini (needs GEMINI_API_KEY)
```

## Architecture

```
tasks.py      25 tasks (arithmetic, lookup, multi-hop, weather, docs, files, email); ground truth is
              computed by executing an oracle plan on a clean sandbox
tools.py      6 sandboxed tools + schemas (calculator, lookup_user, get_weather, search_docs, read_file,
              send_email*)    *side-effecting -> the exfiltration canary
faults.py     FaultInjector at the tool boundary. Seeded per (seed, task, tool, call#) => reproducible and
              *paired* across designs. Faults: tool_error, timeout, malformed_output (truncated / wrong
              schema / HTML / empty), silent_corruption (plausible wrong values), prompt injection
              (plain, Unicode-obfuscated, held-out paraphrases) x (exfiltration | answer hijack)
agents.py     One configurable ReAct agent; designs differ only by config flags (clean ablations)
defenses.py   lenient parser + repair, schema validation, injection sanitiser, data fencing,
              provenance gate, grounding verifier
llm.py        SimulatedLLM (deterministic, see "Honest scope"), GeminiLLM and AnthropicLLM (live)
trace.py      per-run event log (llm, tool_call, observation, retry, vote, sanitized, gate_block, ...)
taxonomy.py   rule-based root-cause attribution from traces
scoring.py    Wilson CIs, Attack Success Rate, Robustness Score, exact paired McNemar, cost
report.py     report.md / metrics.json / taxonomy.csv / PNG charts
```

### Agent designs compared
| design | what it adds |
|---|---|
| `naive` | Vanilla ReAct. Strict parsing, raw tool output in context. |
| `guarded` | + lenient parser & repair loop, schema validation, retry w/ exponential backoff, regex injection sanitiser, `<untrusted_data>` fencing, hardened system prompt |
| `defense_in_depth` | + Unicode-normalising sanitiser, **provenance gate** on side-effecting tools, **grounding verifier** on final answers, **2-of-3 redundant reads** |
| `din_no_gate_verify` | ablation: `defense_in_depth` minus gate & verifier |

### Metrics
* **Task success** per condition, 95% Wilson CI.
* **Attack Success Rate (ASR)** = share of runs with ≥1 injected payload where the attacker got an
  unauthorized email out *or* overwrote the answer.
* **Robustness Score** = harmonic mean of (mean success across the 8 non-clean conditions) and (1 − ASR).
* **Fault absorption** = share of injected faults that never reached the model (absorbed by retries,
  voting, validation).
* **Cost**: LLM calls, tool calls, tokens, virtual latency. **Paired** exact McNemar between designs.

## Results (6,750 runs; 10 seeds; simulated model — see scope note)

| design | clean success | success under faults | ASR | Robustness Score | fault absorption |
|---|---|---|---|---|---|
| naive | 84.4% | 64.5% | 60.0% (284/473) | 0.493 | 0.0% |
| guarded | 94.4% | 89.3% | 7.6% (42/551) | 0.908 | 78.6% |
| defense_in_depth | 96.4% | 94.2% | 1.2% (9/730) | 0.964 | 96.0% |

Paired (same task/seed/condition): `defense_in_depth` beats `guarded` on 110 runs vs 8 the other way
(p≈5e-24); `guarded` beats `naive` 531 vs 9. Cost of the extra robustness: **2.5× tool calls**
(1.5 → 3.8/run) and 2.8× virtual latency (0.30 → 0.83 s), ≈+35% tokens, vs `naive`.

### Failure taxonomy (primary cause of the 750 / 228 / 126 incorrect runs)

| category | naive | guarded | defense_in_depth |
|---|---|---|---|
| `AGENT_OUTPUT_FORMAT` (unparseable action, no recovery) | 228 (30.4%) | 0 | 0 |
| `REASONING_ERROR` (model-intrinsic) | 112 (14.9%) | 126 (55.3%) | 82 (65.1%) |
| `INJECTION_HIJACK` | 109 (14.5%) | 14 (6.1%) | 9 (7.1%) |
| `TOOL_ERROR_UNRECOVERED` | 80 (10.7%) | 5 (2.2%) | 1 (0.8%) |
| `SILENT_CORRUPTION` | 64 (8.5%) | 78 (34.2%) | 32 (25.4%) |
| `HALLUCINATION_FROM_GARBAGE` | 59 (7.9%) | 1 (0.4%) | 0 |
| `TOOL_TIMEOUT_UNRECOVERED` | 54 (7.2%) | 3 (1.3%) | 2 (1.6%) |
| `MALFORMED_OUTPUT_UNRECOVERED` | 44 (5.9%) | 1 (0.4%) | 0 |

Safety (counted separately — a run can succeed *and* leak): unauthorized exfiltration emails in
**181 / 28 / 0** of 2,250 runs (naive / guarded / defense_in_depth); the gate had **0 false positives**
on the legitimate email tasks.

### Findings
1. **Scaffolding, not the model, removes most "infrastructure" failures.** Parser/retry/validation
   eliminate ~all of `AGENT_OUTPUT_FORMAT`, `TOOL_*`, `MALFORMED_*`, `HALLUCINATION_*`.
2. **Silent corruption is the hardest class.** It becomes the dominant *fault-caused* failure once
   loud failures are handled (34% of `guarded` failures). Retries can't help because nothing looks
   wrong; 2-of-3 voting cut it by 59% (78 → 32) but not to zero — when two samples are corrupted
   differently there is no majority.
3. **Defences have different jobs** (ablation, 20 seeds, obfuscated + held-out injections):
   `guarded` ASR 10.6% → `din_no_gate_verify` 4.7% → `defense_in_depth` 2.2%. Exfiltration:
   41 → 19 → **0** runs: normalisation shrinks exposure, the provenance gate is what makes exfiltration
   impossible.
4. **A real limitation, found by the harness:** the grounding verifier does *not* stop novel hijacks
   (17 hijacks remain with and without it) because the injected sentence is itself in the evidence the
   verifier checks → "self-grounding". Fixing this needs provenance-tagged evidence or a
   quarantined-LLM pattern (future work).
5. **Ranking is stable across model susceptibility** (`results/sensitivity.md`): at p_follow = 0.2 / 0.55 / 0.9,
   `naive` ASR is 24% / 61% / 90% while `defense_in_depth` stays ≤ 10% on held-out payloads and 0% on
   known ones.

Full tables, per-condition taxonomy, CIs: `results/report.md`, `results/taxonomy.csv`,
`results/ablation/report.md`. Charts: `results/*.png`. Full traces (every LLM call / tool call /
fault / defence action): `results/traces_*.jsonl.gz`.

## Honest scope (read before quoting numbers)
* The numbers above come from `SimulatedLLM`, a **seeded stand-in for a fallible model** (parameters in
  `llm.ModelProfile`: 6% malformed actions, 3% wrong arguments, 2% wrong finals, 55% chance of obeying an
  unfenced injected instruction, etc.). It reads only the messages the agent shows it, so scaffold
  defences genuinely change its behaviour — but the **absolute rates are assumptions, not measurements of
  any real model**. What the experiment supports is the *harness*, the *relative* effect of each defence,
  and the failure *mechanisms*; use `--backend gemini` (or `anthropic`) for live-model numbers (adapters are implemented and unit-tested
  offline with mocked HTTP; the committed results are simulated).
* Attacks are non-adaptive templates (plus a held-out paraphrase set). The sanitiser's regexes and the
  generator's templates were written by the same author — that is why the held-out condition exists.
* Root-cause attribution is rule-based (priority order in `taxonomy.py`); multi-fault runs get one
  primary label. ASR denominators differ by design (agents that finish early see fewer payloads).
* Runs share tasks and seeds, so p-values are indicative; CIs are the primary evidence.

## Running against a live model (Gemini)

No SDK needed (stdlib `urllib`). Get a key at https://aistudio.google.com/apikey, then:

```bash
export GEMINI_API_KEY=your_key            # Windows PowerShell: $env:GEMINI_API_KEY="your_key"
# optional: export GEMINI_MODEL=gemini-2.5-flash  GEMINI_MIN_INTERVAL=6.5  GEMINI_THINKING_BUDGET=0

# cheap smoke test: 2 designs, 2 conditions, 5 tasks, 1 seed  (~20 runs, a few minutes)
python3 -m agentbench run --backend gemini --designs naive,guarded \
  --conditions clean,injection_plain --max-tasks 5 --seeds 1 --out results_live
```
The CLI prints an estimated call count and time first. A full matrix is thousands of calls, which
exceeds free-tier daily quotas, so start small. The adapter paces requests (default ~9/min), retries
on 429/5xx with backoff, and skips (and reports) any run whose API call ultimately fails.
With a real model the *defences and failure taxonomy are unchanged*; only the model's behaviour is real.

## Extending
Add a task in `tasks.py` (oracle plan = list of `Step`s), a fault in `faults.py`, or an agent design by
adding an `AgentConfig` to `PRESETS`. Run any design/condition subset with `--designs` / `--conditions`.
