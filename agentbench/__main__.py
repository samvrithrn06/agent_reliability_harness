"""CLI:  python -m agentbench run --seeds 10 --out results
        python -m agentbench sweep   (sensitivity to simulated-model injection susceptibility)"""
from __future__ import annotations

import argparse
import dataclasses

from . import report, runner, scoring
from .agents import DEFAULT_DESIGNS, PRESETS
from .faults import CONDITIONS
from .llm import ModelProfile
from .tasks import TASKS


def sweep(out: str, seeds: int):
    conds = ["injection_plain", "injection_obfuscated", "injection_novel"]
    designs = ["naive", "guarded", "din_no_gate_verify", "defense_in_depth"]
    lines = ["# Sensitivity: attack success rate vs. simulated model susceptibility\n",
             f"p_follow_injection = P(model obeys an unfenced injected instruction). {seeds} seeds x "
             f"{len(TASKS)} tasks per cell; ASR = attacks succeeding / runs with >=1 payload.\n",
             "| p_follow | design | " + " | ".join(f"ASR {c.replace('injection_', '')}" for c in conds) + " |",
             "|---|---|" + "---|" * len(conds)]
    for pf in (0.2, 0.55, 0.9):
        prof = dataclasses.replace(ModelProfile(), p_follow_injection=pf)
        recs = runner.run_matrix(designs, conds, list(range(seeds)), out + "/_sweep_tmp", profile=prof, quiet=True)
        for d in designs:
            cells = []
            for c in conds:
                rr = [r for r in recs if r["design"] == d and r["condition"] == c and r["injected"] > 0]
                k = sum(r["attack_success"] for r in rr)
                lo, hi = scoring.wilson(k, len(rr))
                cells.append(f"{100*k/max(1,len(rr)):.1f}% [{100*lo:.0f},{100*hi:.0f}]")
            lines.append(f"| {pf} | {d} | " + " | ".join(cells) + " |")
    md = "\n".join(lines) + "\n"
    open(f"{out}/sensitivity.md", "w").write(md)
    print(md)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the experiment matrix and write a report")
    r.add_argument("--designs", default=",".join(DEFAULT_DESIGNS), help=f"choices: {','.join(PRESETS)}")
    r.add_argument("--conditions", default=",".join(CONDITIONS))
    r.add_argument("--seeds", type=int, default=10)
    r.add_argument("--backend", default="simulated", choices=["simulated", "gemini", "anthropic"])
    r.add_argument("--max-tasks", type=int, default=None, help="use only the first N tasks (cheap live runs)")
    r.add_argument("--out", default="results")
    r.add_argument("--quiet", action="store_true")
    q = sub.add_parser("report", help="re-score existing traces")
    q.add_argument("--out", default="results")
    s = sub.add_parser("sweep", help="sensitivity sweep over simulated injection susceptibility")
    s.add_argument("--out", default="results")
    s.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args(argv)

    if a.cmd == "run":
        seeds = list(range(a.seeds))
        tasks = TASKS[:: max(1, len(TASKS) // a.max_tasks)][: a.max_tasks] if a.max_tasks else TASKS
        n_runs = len(a.designs.split(",")) * len(a.conditions.split(",")) * len(tasks) * len(seeds)
        if a.backend != "simulated":
            from .llm import make_llm
            try:
                make_llm(a.backend, tasks[0], 0)     # fail fast on missing key / SDK
            except (RuntimeError, ImportError) as e:
                raise SystemExit(f"error: {e}")
            print(f"Live run: {n_runs} runs, roughly {int(n_runs * 3)} API calls "
                  f"(~{n_runs * 3 * 6.5 / 60:.0f} min at the default rate limit).", flush=True)
        recs = runner.run_matrix(a.designs.split(","), a.conditions.split(","), seeds, a.out,
                                 a.backend, tasks=tasks, quiet=a.quiet)
        if not recs:
            raise SystemExit("error: no runs completed (see warnings above)")
        print(report.write_all(scoring.compute(recs), a.out, len(tasks), seeds, a.backend))
    elif a.cmd == "report":
        recs = runner.load_records(a.out)
        seeds = sorted({r["seed"] for r in recs})
        print(report.write_all(scoring.compute(recs), a.out, len({r["task"] for r in recs}), seeds, "from-traces"))
    else:
        sweep(a.out, a.seeds)


if __name__ == "__main__":
    main()
