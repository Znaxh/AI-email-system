"""Batch pipeline: generate holdout replies, score with RAGAS, report reliability.

Usage:
    python pipeline.py --all           # full run
    python pipeline.py --generate      # only generation
    python pipeline.py --evaluate      # only RAGAS evaluation
    python pipeline.py --validate      # reliability report from feedback_events
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.company_data.service import load_active_company_bundle
from src.config import load_config
from src.evaluator import evaluate_generated
from src.generator import generate_reply
from src.storage.factory import get_blob_store
from src.validate_metric import validate

RESULTS = Path("results")
console = Console()


def load_data():
    """Load the active company bundle (no hardcoded data/ paths)."""
    cfg = load_config()
    bundle = load_active_company_bundle(
        allow_empty=False,
        config=cfg,
        include_user_examples=False,  # batch eval never includes Settings examples
    )
    return bundle


def _put_json(key: str, obj) -> None:
    RESULTS.mkdir(exist_ok=True)
    raw = json.dumps(obj, indent=2)
    # Keep a local mirror for tooling that still opens Path directly.
    local = Path(key)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(raw)
    try:
        get_blob_store().put(key, raw, "application/json")
    except Exception:
        pass


def _get_json(key: str):
    try:
        return json.loads(get_blob_store().get(key).decode("utf-8"))
    except Exception:
        path = Path(key)
        if path.exists():
            return json.loads(path.read_text())
        raise


def run_generate(bundle, limit: int = 0) -> list[dict]:
    transactions = bundle.transactions
    tickets = bundle.tickets
    policy_store = bundle.policy_store
    retriever = bundle.retriever

    holdout = [t for t in tickets if t.split == "holdout"]
    # Exclude holdout tickets whose order link was cleared (unknown order_id).
    runnable = []
    skipped = []
    for t in holdout:
        if not t.order_id or t.order_id not in transactions:
            skipped.append(t.ticket_id)
            continue
        runnable.append(t)
    if skipped:
        console.print(
            f"[yellow]skipping {len(skipped)} holdout ticket(s) with unlinked/missing orders: "
            f"{', '.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}[/]"
        )
    if limit:
        runnable = runnable[:limit]

    generated = []
    for t in runnable:
        console.print(f"  generating reply for [bold]{t.ticket_id}[/] ({t.category})...")
        g = generate_reply(
            t.incoming_email,
            transactions[t.order_id],
            policy_store,
            retriever,
            ticket_id=t.ticket_id,
            category=t.category,
        )
        dump = g.model_dump()
        dump["company_data_version"] = bundle.version_id
        generated.append(dump)

    _put_json("results/generated_replies.json", generated)
    console.print(f"[green]wrote results/generated_replies.json ({len(generated)} replies)[/]")
    return generated


def run_evaluate(bundle, limit: int = 0) -> list[dict]:
    transactions = bundle.transactions
    tickets_by_id = {t.ticket_id: t for t in bundle.tickets}
    policy_store = bundle.policy_store
    generated = _get_json("results/generated_replies.json")
    if limit:
        generated = generated[:limit]

    results = []
    for g in generated:
        t = tickets_by_id.get(g["ticket_id"])
        if t is None:
            console.print(f"[yellow]skip unknown ticket {g['ticket_id']}[/]")
            continue
        if not t.order_id or t.order_id not in transactions:
            console.print(f"[yellow]skip {t.ticket_id}: no transaction[/]")
            continue
        console.print(f"  RAGAS-scoring reply for [bold]{t.ticket_id}[/]...")
        from src.schema import GeneratedReply, Remedy

        gen = GeneratedReply(
            ticket_id=g["ticket_id"],
            response_id=g.get("response_id") or g["ticket_id"],
            reply=g["reply"],
            remedy=Remedy(**(g.get("remedy") or {})),
            retrieved_policy_chunks=g.get("retrieved_policy_chunks") or [],
            retrieved_rule_ids=g.get("retrieved_rule_ids") or [],
            cited_rule_ids=g.get("cited_rule_ids") or [],
            retrieved_similar_tickets=g.get("retrieved_similar_tickets") or [],
        )
        r = evaluate_generated(t, transactions[t.order_id], gen, policy_store)
        dump = r.model_dump()
        dump["company_data_version"] = bundle.version_id
        results.append(dump)

    _put_json("results/evaluation_results.json", results)
    console.print(f"[green]wrote results/evaluation_results.json ({len(results)} evaluations)[/]")
    return results


def run_validate() -> dict:
    report = validate()
    _put_json("results/validation_report.json", report)
    console.print("[green]wrote results/validation_report.json[/]")
    return report


def print_summary(results: list[dict], report: dict):
    console.rule("[bold]Summary — Response Quality (RAGAS)")
    scored = [r for r in results if r.get("faithfulness") is not None]
    if scored:
        avg_f = sum(r["faithfulness"] for r in scored) / len(scored)
        avg_q = sum((r.get("quality_score") or 0) for r in scored) / len(scored)
        gated = sum(1 for r in results if r.get("gated_from_auto"))
        console.print(f"Faithfulness avg: [bold]{avg_f:.3f}[/] (n={len(scored)})")
        console.print(f"Routing quality_score avg: [bold]{avg_q:.3f}[/]")
        console.print(f"Gated from AUTO: [bold]{gated}[/] / {len(results)}")

        by_cat = defaultdict(list)
        for r in scored:
            by_cat[r.get("category") or "?"].append(r["faithfulness"])
        table = Table(title="Faithfulness by category")
        table.add_column("category")
        table.add_column("avg", justify="right")
        table.add_column("n", justify="right")
        for cat, vals in sorted(by_cat.items()):
            table.add_row(cat, f"{sum(vals)/len(vals):.3f}", str(len(vals)))
        console.print(table)
    else:
        console.print("[yellow]No RAGAS scores available (scoring errors or empty run).[/]")

    console.rule("[bold]Summary — System Reliability (Human Feedback)")
    crit = report.get("critical_error_rate") or {}
    rel = report.get("reliability_rate") or {}
    cov = report.get("audit_coverage") or {}
    if crit.get("insufficient_data"):
        console.print(
            f"Critical error rate: [yellow]insufficient data[/] "
            f"(n={crit.get('n', 0)}, need ≥20 labeled AUTO)"
        )
    else:
        console.print(
            f"Critical error rate: [bold]{crit.get('rate')}[/] "
            f"(n={crit.get('n')}, CI {crit.get('ci_low')}–{crit.get('ci_high')})"
        )
    if rel.get("insufficient_data"):
        console.print(f"Reliability rate: [yellow]insufficient data[/] (n={rel.get('n', 0)})")
    else:
        console.print(
            f"Reliability rate: [bold]{rel.get('rate')}[/] "
            f"(n={rel.get('n')}, CI {rel.get('ci_low')}–{rel.get('ci_high')})"
        )
    console.print(
        f"Audit coverage (labeled AUTO / all AUTO): "
        f"{cov.get('labeled_auto')}/{cov.get('all_auto')}"
    )
    console.print(f"Feedback events: [bold]{report.get('n_feedback_events', 0)}[/]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Trial mode: only the first N holdout tickets.",
    )
    args = parser.parse_args()
    if not any([args.all, args.generate, args.evaluate, args.validate]):
        parser.print_help()
        sys.exit(1)

    try:
        bundle = load_data()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        console.print("Complete company-data setup in Settings (policy + transactions) first.")
        sys.exit(2)

    console.print(f"Active company data version: [bold]{bundle.version_id}[/]")

    results = None
    if args.all or args.generate:
        console.rule("[bold]1. Generate")
        run_generate(bundle, limit=args.limit)
    if args.all or args.evaluate:
        console.rule("[bold]2. Evaluate (RAGAS)")
        results = run_evaluate(bundle, limit=args.limit)
    if args.all or args.validate:
        console.rule("[bold]3. Reliability report")
        report = run_validate()
        if results is None:
            try:
                results = _get_json("results/evaluation_results.json")
            except Exception:
                results = []
        print_summary(results, report)


if __name__ == "__main__":
    main()
