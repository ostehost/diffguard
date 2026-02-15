"""Benchmark fixture configuration."""

import pytest


@pytest.fixture()
def benchmark_config(benchmark):  # type: ignore[no-untyped-def]
    """Configure benchmark defaults."""
    benchmark.group = "diffguard"
    benchmark.warmup = True
    return benchmark
