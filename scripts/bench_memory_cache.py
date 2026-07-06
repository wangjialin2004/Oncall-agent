"""Benchmark the L1 memory cache against the raw SQLite path.

Reproduces the speed-up claimed in plan §P5: with the cache warm, the
``format_for_prompt`` / ``lookup`` / ``list`` hot paths should hold a flat
tail latency regardless of the underlying row count, while the raw path
grows linearly.

Usage::

    uv run python scripts/bench_memory_cache.py --rows 500,5000
    uv run python scripts/bench_memory_cache.py --rows 500 --iters 5000

The script seeds a temporary SQLite database, measures the cold (no-cache)
and warm (cache-hit) tails for each operation, and prints a small table.

It deliberately avoids touching the production ``volumes/long_term_memory.db``
— the goal is to measure relative speed-up, not to load-test the real DB.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable

# Allow running this script from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.services.experience_memory_service import ExperienceMemoryService
from app.services.memory_cache import get_default_cache, reset_default_cache
from app.services.service_knowledge_service import ServiceKnowledgeService
from app.services.user_preference_service import UserPreferenceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _temp_db():
    tmp = Path(tempfile.mkdtemp(prefix="bench-memory-cache-"))
    db = tmp / "bench.db"
    try:
        yield db
    finally:
        # Windows holds an mmap handle on the SQLite file; an explicit
        # ``gc.collect`` lets the test fixture release it before unlink.
        import gc

        gc.collect()
        for _ in range(5):
            if not db.exists():
                break
            try:
                db.unlink()
            except PermissionError:
                time.sleep(0.05)
            else:
                break
        try:
            tmp.rmdir()
        except OSError:
            pass


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _summarise(samples: list[float]) -> dict[str, float]:
    return {
        "n": len(samples),
        "p50_ms": _percentile(samples, 0.50) * 1000,
        "p95_ms": _percentile(samples, 0.95) * 1000,
        "mean_ms": (statistics.fmean(samples) * 1000) if samples else 0.0,
    }


def _time(callable_: Callable[[], None], iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        callable_()
        samples.append(time.perf_counter() - t0)
    return samples


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _seed_experience(db: Path, rows: int) -> ExperienceMemoryService:
    service = ExperienceMemoryService(db_path=db, index_service=None)
    for i in range(rows):
        service.create_manual(
            project_id="bench",
            symptoms=f"symptom-{i} database pool exhausted in api-{i % 5}",
            root_cause=f"cause-{i} connection leak in worker pool",
            resolution=f"resolution-{i} recycle connection pool",
            environment="prod",
            service_name=f"svc-{i % 5}",
            confidence=0.7,
        )
    return service


def _seed_service_knowledge(db: Path, rows: int) -> ServiceKnowledgeService:
    service = ServiceKnowledgeService(db_path=db)
    for i in range(rows):
        service.upsert_service(
            project_id="bench",
            service_name=f"svc-{i}",
            environment="prod",
            owner_team=f"team-{i % 7}",
        )
        service.upsert_baseline(
            project_id="bench",
            service_name=f"svc-{i}",
            environment="prod",
            metric_name="cpu",
            min_value=0.0,
            max_value=1.0,
        )
    return service


def _seed_user_preference(db: Path) -> UserPreferenceService:
    service = UserPreferenceService(db_path=db)
    for i in range(50):
        service.upsert(
            owner_key=f"user-{i}",
            default_environment="prod",
            language="zh-CN",
            detail_level="normal",
            focused_services=[f"svc-{i % 5}"],
        )
    return service


# ---------------------------------------------------------------------------
# Benchmark scenarios
# ---------------------------------------------------------------------------


def _bench_format_for_prompt(service: UserPreferenceService, owner_key: str, iters: int) -> dict:
    reset_default_cache()
    # Cold path: first call populates the cache; subsequent calls are hits.
    service.format_for_prompt(owner_key)
    cold = _time(lambda: service.format_for_prompt(owner_key), iters)

    reset_default_cache()
    # No-cache path: invalidate before every call so the cache stays cold.
    def cold_call() -> None:
        cache = get_default_cache()
        cache.invalidate_prefix(f"memory:")
        service.format_for_prompt(owner_key)

    warm = _time(cold_call, iters)
    return {"cached": _summarise(cold), "uncached": _summarise(warm)}


def _bench_lookup(svc: ServiceKnowledgeService, iters: int) -> dict:
    reset_default_cache()
    # Warm up
    svc.lookup(project_id="bench", service_name="svc-1", environment="prod")
    cached = _time(
        lambda: svc.lookup(project_id="bench", service_name="svc-1", environment="prod"),
        iters,
    )

    reset_default_cache()
    def cold_call() -> None:
        cache = get_default_cache()
        cache.invalidate_prefix(f"memory:")
        svc.lookup(project_id="bench", service_name="svc-1", environment="prod")

    uncached = _time(cold_call, iters)
    return {"cached": _summarise(cached), "uncached": _summarise(uncached)}


def _bench_list(exp: ExperienceMemoryService, iters: int) -> dict:
    reset_default_cache()
    exp.list(project_id="bench", enabled=True, limit=1000)
    cached = _time(
        lambda: exp.list(project_id="bench", enabled=True, limit=1000),
        iters,
    )

    reset_default_cache()
    def cold_call() -> None:
        cache = get_default_cache()
        cache.invalidate_prefix(f"memory:")
        exp.list(project_id="bench", enabled=True, limit=1000)

    uncached = _time(cold_call, iters)
    return {"cached": _summarise(cached), "uncached": _summarise(uncached)}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_row(name: str, scenario: str, stats: dict) -> str:
    return (
        f"  {name:<22} {scenario:<10} "
        f"P50={stats['p50_ms']:>8.3f}ms  P95={stats['p95_ms']:>8.3f}ms  "
        f"mean={stats['mean_ms']:>8.3f}ms  (n={stats['n']})"
    )


def _print_table(label: str, rows: int, scenarios: dict) -> None:
    print(f"\n=== {label} (rows={rows}) ===")
    for op, results in scenarios.items():
        for scenario in ("cached", "uncached"):
            print(_format_row(op, scenario, results[scenario]))
        # Speed-up summary
        speedup = (
            results["uncached"]["p95_ms"] / results["cached"]["p95_ms"]
            if results["cached"]["p95_ms"] > 0
            else float("inf")
        )
        print(f"  -> P95 speed-up: {speedup:.2f}x")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        type=str,
        default="500,5000",
        help="comma-separated row counts to seed (default: 500,5000)",
    )
    parser.add_argument(
        "--iters",
        type=int,
        default=2000,
        help="iterations per measurement (default: 2000)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    row_counts = sorted({int(x) for x in args.rows.split(",") if x.strip()})

    for rows in row_counts:
        with _temp_db() as db:
            print(f"\n# Seeding {rows} experience rows + {rows} service rows")
            exp = _seed_experience(db, rows)
            svc = _seed_service_knowledge(db, rows)
            ups = _seed_user_preference(db)

            _print_table(
                "Hot paths",
                rows,
                {
                    "format_for_prompt": _bench_format_for_prompt(ups, "user-1", args.iters),
                    "svc:lookup": _bench_lookup(svc, args.iters),
                    "exp:list": _bench_list(exp, args.iters),
                },
            )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
