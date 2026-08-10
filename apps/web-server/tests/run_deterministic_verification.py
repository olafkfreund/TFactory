#!/usr/bin/env python3
"""
Deterministic verification script for file locking tests.

This script runs each of the 6 re-enabled concurrency tests 20 times to ensure
they pass deterministically with zero JSONDecodeError failures.

AC3: The re-enabled tests pass deterministically: running each re-enabled test
20 times in a loop produces zero JSONDecodeError failures.
"""

import json
import subprocess
import sys
from pathlib import Path


def run_test(test_path: str, run_number: int) -> bool:
    """Run a single test and return True if it passed."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-xvs"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        print(f"Error running test: {e}")
        return False


def main():
    """Run deterministic verification."""
    # List of re-enabled tests to verify
    tests = [
        "apps/web-server/tests/test_performance.py::TestFileLocking::test_concurrent_profile_updates",
        "apps/web-server/tests/test_performance.py::TestFileLocking::test_concurrent_api_profile_creation",
        "apps/web-server/tests/test_performance.py::TestFileLocking::test_concurrent_ideation_updates",
        "apps/web-server/tests/test_performance.py::TestConcurrentAccess::test_concurrent_mixed_operations",
        "apps/web-server/tests/test_performance.py::TestConcurrentAccess::test_concurrent_different_endpoints",
        "apps/web-server/tests/test_performance.py::TestAPIRateLimits::test_concurrent_rate_limit_handling",
    ]

    total_runs = 20
    all_passed = True
    results = {}

    print("=" * 80)
    print("DETERMINISTIC VERIFICATION - Running 6 tests × 20 times each")
    print("=" * 80)

    for test in tests:
        test_name = test.split("::")[-1]
        passed_count = 0
        failed_count = 0

        print(f"\nTesting: {test_name}")
        print("-" * 80)

        for run in range(1, total_runs + 1):
            success = run_test(test, run)
            if success:
                passed_count += 1
                status = "✅"
            else:
                failed_count += 1
                status = "❌"
                all_passed = False

            # Print progress every 5 runs
            if run % 5 == 0 or run == 1:
                print(f"  Run {run:2d}/{total_runs}: {status}")

        results[test_name] = {
            "total": total_runs,
            "passed": passed_count,
            "failed": failed_count,
        }

        print(f"  Result: {passed_count}/{total_runs} passed")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total_all = len(tests) * total_runs
    total_passed = sum(r["passed"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())

    for test_name, result in results.items():
        status = "✅ PASS" if result["failed"] == 0 else "❌ FAIL"
        print(f"{status}: {test_name} - {result['passed']}/{result['total']}")

    print("-" * 80)
    print(
        f"Total: {total_passed}/{total_all} passed ({100*total_passed//total_all}%)"
    )

    if all_passed:
        print("\n✅ All 6 tests passed deterministically (20 runs each = zero failures)")
        return 0
    else:
        print(f"\n❌ {total_failed} failures detected - file locking may not be working")
        return 1


if __name__ == "__main__":
    sys.exit(main())
