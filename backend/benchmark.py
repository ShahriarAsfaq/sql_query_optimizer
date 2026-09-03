#!/usr/bin/env python
"""
Performance benchmarking script for SQL Query Optimizer API.
Run with: python benchmark.py
"""

import time
import json
import statistics
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000/api"

# Test queries for benchmarking
TEST_QUERIES = {
    "simple_select": "SELECT * FROM employees WHERE department_id = 1",
    "aggregate": "SELECT department_id, AVG(salary) FROM employees GROUP BY department_id",
    "join": "SELECT e.*, d.name FROM employees e JOIN departments d ON e.department_id = d.id",
    "complex": "SELECT d.name, AVG(e.salary), COUNT(*) FROM employees e JOIN departments d ON e.department_id = d.id GROUP BY d.name HAVING AVG(e.salary) > 50000 ORDER BY AVG(e.salary) DESC LIMIT 10",
    "subquery": "SELECT * FROM employees WHERE salary > (SELECT AVG(salary) FROM employees)",
}

# Schema for generate endpoint (columns as dict, not list)
GENERATE_SCHEMA = {
    "tables": {
        "employees": {
            "columns": {
                "id": {"type": "integer"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "salary": {"type": "integer"},
                "department_id": {"type": "integer"}
            }
        },
        "departments": {
            "columns": {
                "id": {"type": "integer"},
                "name": {"type": "string"}
            }
        }
    }
}

TEST_NATURAL_LANGUAGE = [
    "Show all employees in the Engineering department",
    "Top 5 highest paid employees with their department names",
    "Average salary by department",
    "Employees hired after 2020 with their managers",
    "Count of employees per department",
    "Show me the top 10 highest paid employees",
    "List all customers from New York",
    "Show total sales by product category",
]

HEADERS = {"Content-Type": "application/json", "Authorization": "Api-Key dev-key-12345"}


def benchmark_endpoint(endpoint: str, payload: dict, iterations: int = 10) -> dict:
    """Benchmark a single endpoint with multiple iterations."""
    times = []
    errors = 0

    for _ in range(iterations):
        start = time.perf_counter()
        try:
            response = requests.post(f"{BASE_URL}{endpoint}", json=payload, headers=HEADERS, timeout=30)
            elapsed = time.perf_counter() - start
            if response.status_code == 200:
                times.append(elapsed)
            else:
                errors += 1
        except Exception:
            errors += 1

    if not times:
        return {"error": "All requests failed", "errors": errors}

    return {
        "iterations": iterations,
        "successful": len(times),
        "errors": errors,
        "min_ms": round(min(times) * 1000, 2),
        "max_ms": round(max(times) * 1000, 2),
        "mean_ms": round(statistics.mean(times) * 1000, 2),
        "median_ms": round(statistics.median(times) * 1000, 2),
        "stdev_ms": round(statistics.stdev(times) * 1000, 2) if len(times) > 1 else 0,
    }


def benchmark_concurrent(endpoint: str, payload: dict, concurrency: int = 5, total_requests: int = 20) -> dict:
    """Benchmark endpoint with concurrent requests."""
    times = []
    errors = 0

    def make_request():
        start = time.perf_counter()
        try:
            response = requests.post(f"{BASE_URL}{endpoint}", json=payload, headers=HEADERS, timeout=30)
            elapsed = time.perf_counter() - start
            if response.status_code == 200:
                return elapsed
            return None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(make_request) for _ in range(total_requests)]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                times.append(result)
            else:
                errors += 1

    if not times:
        return {"error": "All requests failed", "errors": errors}

    return {
        "concurrency": concurrency,
        "total_requests": total_requests,
        "successful": len(times),
        "errors": errors,
        "min_ms": round(min(times) * 1000, 2),
        "max_ms": round(max(times) * 1000, 2),
        "mean_ms": round(statistics.mean(times) * 1000, 2),
        "median_ms": round(statistics.median(times) * 1000, 2),
        "stdev_ms": round(statistics.stdev(times) * 1000, 2) if len(times) > 1 else 0,
        "throughput_rps": round(len(times) / sum(times), 2),
    }


def run_benchmarks():
    """Run all benchmarks and print results."""
    print("=" * 70)
    print("SQL Query Optimizer - Performance Benchmarks")
    print("=" * 70)
    print()

    # Check server health
    try:
        response = requests.get(f"{BASE_URL}/queries/health/", timeout=5)
        if response.status_code != 200:
            print(f"[ERROR] Server health check failed: {response.status_code}")
            return
        print(f"[OK] Server is healthy: {response.json()}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to server: {e}")
        print("   Make sure the backend is running on http://localhost:8000")
        return

    print()

    # Sequential benchmarks
    print("[BENCH] SEQUENTIAL BENCHMARKS (10 iterations each)")
    print("-" * 70)

    # Analyze endpoint
    print("\n[ANALYZE] POST /api/queries/analyze/")
    for name, query in TEST_QUERIES.items():
        result = benchmark_endpoint("/queries/analyze/", {"sql": query, "explain": False}, 10)
        if "error" in result:
            print(f"  {name}: {result['error']}")
        else:
            print(f"  {name:20s} | mean: {result['mean_ms']:>8.2f}ms | median: {result['median_ms']:>8.2f}ms | min: {result['min_ms']:>7.2f}ms | max: {result['max_ms']:>7.2f}ms")

    # Optimize endpoint
    print("\n[OPTIMIZE] POST /api/queries/optimize/")
    for name, query in TEST_QUERIES.items():
        result = benchmark_endpoint("/queries/optimize/", {"sql": query, "dialect": "postgresql"}, 10)
        if "error" in result:
            print(f"  {name}: {result['error']}")
        else:
            print(f"  {name:20s} | mean: {result['mean_ms']:>8.2f}ms | median: {result['median_ms']:>8.2f}ms | min: {result['min_ms']:>7.2f}ms | max: {result['max_ms']:>7.2f}ms")

    # Generate endpoint
    print("\n[GENERATE] POST /api/queries/generate/")
    for i, nl in enumerate(TEST_NATURAL_LANGUAGE):
        result = benchmark_endpoint("/queries/generate/", {"intent_text": nl, "schema": GENERATE_SCHEMA}, 10)
        if "error" in result:
            print(f"  query_{i+1}: {result['error']}")
        else:
            print(f"  query_{i+1:<2}        | mean: {result['mean_ms']:>8.2f}ms | median: {result['median_ms']:>8.2f}ms | min: {result['min_ms']:>7.2f}ms | max: {result['max_ms']:>7.2f}ms")

    # Concurrent benchmarks
    print("\n" + "=" * 70)
    print("[BENCH] CONCURRENT BENCHMARKS (5 workers, 20 requests each)")
    print("-" * 70)

    test_query = TEST_QUERIES["complex"]

    print("\n[ANALYZE] POST /api/queries/analyze/ (concurrent)")
    result = benchmark_concurrent("/queries/analyze/", {"sql": test_query, "explain": False}, 5, 20)
    if "error" not in result:
        print(f"  throughput: {result['throughput_rps']:.2f} req/s | mean: {result['mean_ms']:.2f}ms | median: {result['median_ms']:.2f}ms")

    print("\n[OPTIMIZE] POST /api/queries/optimize/ (concurrent)")
    result = benchmark_concurrent("/queries/optimize/", {"sql": test_query, "dialect": "postgresql"}, 5, 20)
    if "error" not in result:
        print(f"  throughput: {result['throughput_rps']:.2f} req/s | mean: {result['mean_ms']:.2f}ms | median: {result['median_ms']:.2f}ms")

    print("\n[GENERATE] POST /api/queries/generate/ (concurrent)")
    result = benchmark_concurrent("/queries/generate/", {"intent_text": TEST_NATURAL_LANGUAGE[0], "schema": GENERATE_SCHEMA}, 5, 20)
    if "error" not in result:
        print(f"  throughput: {result['throughput_rps']:.2f} req/s | mean: {result['mean_ms']:.2f}ms | median: {result['median_ms']:.2f}ms")

    print("\n" + "=" * 70)
    print("[DONE] Benchmarks complete")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmarks()