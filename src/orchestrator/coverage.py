"""
This module is responsible for (pre-)computing coverage information for
baselines A and B.
"""
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from timeit import default_timer as timer
import functools
import os
import logging
import json

import bugzoo.server
from bugzoo.client import Client as BugZooClient
from bugzoo.core.bug import Bug as Snapshot
from bugzoo.core.test import TestCase
from bugzoo.core.coverage import TestCoverage, TestSuiteCoverage
from bugzoo.exceptions import BugZooException
from boggart import Client as BoggartClient
from boggart import Mutant

from .orchestrator import fetch_instrumentation_snapshot
from .exceptions import FailedToComputeCoverage
from .blacklist import is_file_mutable
from .snapshot import fetch_instrumentation_snapshot

logger = logging.getLogger(__name__)  # type: logging.Logger
logger.setLevel(logging.DEBUG)

__BASELINE_COVERAGE = None  #  type: Optional[TestSuiteCoverage]

BASELINE_COVERAGE_FN = \
    os.path.join(os.path.dirname(__file__),
                 'data/baseline.coverage.json')  # type: str


def compute_mutant_coverage(client_bugzoo: BugZooClient,
                            client_boggart: BoggartClient,
                            mutant: Mutant,
                            *,
                            threads: int = 6
                            ) -> TestSuiteCoverage:
    logger.info("computing coverage for mutant: %s", mutant)
    coverage_baseline = load_baseline_coverage()
    snapshot_mutant = client_bugzoo.bugs[mutant.snapshot]

    # restrict to tests that cover the perturbed file
    filename = list(mutant.mutations)[0].location.filename
    tests = [t for t in snapshot_mutant.tests \
             if filename in coverage_baseline[t.name].lines.files]
    logger.info("restricting coverage for mutant to following tests: %s",
                ', '.join([t.name for t in tests]))

    mutant_instrumented = None
    try:
        logger.info("creating temporary instrumented mutant")
        mutant_instrumented = \
            client_boggart.mutate(fetch_instrumentation_snapshot(client_bugzoo),
                                  mutant.mutations)
        snapshot_instrumented = \
            client_bugzoo.bugs[mutant_instrumented.snapshot]
        logger.info("created temporary instrumented mutant: %s",
                    mutant_instrumented)
        coverage = compute_coverage(client_bugzoo,
                                    snapshot_instrumented,
                                    tests,
                                    threads=threads)
    except Exception:
        logger.warning("failed to compute coverage for mutant: %s", mutant)
        raise FailedToComputeCoverage

    finally:
        if mutant_instrumented:
            del client_boggart.mutants[mutant_instrumented.uuid]

    # complete the rest of the coverage report
    test_to_coverage = {}  # type: Dict[str, TestCoverage]
    for test_name in coverage_baseline:
        test_to_coverage[test_name] = coverage_baseline[test_name]
    for test_name in coverage:
        test_to_coverage[test_name] = coverage[test_name]
    coverage = TestSuiteCoverage(test_to_coverage)

    logger.info("computed coverage for mutant: %s", mutant)
    return coverage


# FIXME stop provisioning containers -- have one for each thread
def compute_test_coverage(client_bugzoo: BugZooClient,
                          snapshot: Snapshot,
                          test: TestCase
                          ) -> TestCoverage:
    """
    Computes coverage information for a given test using a fresh container.
    """
    logger.info("getting coverage for test: %s", test.name)
    ctr_mgr = client_bugzoo.containers
    container = None
    try:
        container = ctr_mgr.provision(snapshot)
        outcome = ctr_mgr.test(container, test)
        lines = ctr_mgr.extract_coverage(container)
        lines = lines.filter(lambda ln: is_file_mutable(ln.filename))
        return TestCoverage(test.name, outcome, lines)
    except BugZooException:
        logger.exception("failed to compute coverage for snapshot (%s) on test (%s).",
                         snapshot.name, test.name)
        raise FailedToComputeCoverage
    finally:
        if container is not None:
            del ctr_mgr[container.uid]


def compute_coverage(client_bugzoo: BugZooClient,
                     snapshot: Snapshot,
                     tests: List[TestCase],
                     *,
                     threads: int = 6
                     ) -> TestSuiteCoverage:
    t_start = timer()
    logger.debug("computing coverage")
    with ThreadPoolExecutor(max_workers=threads) as executor:
        func_coverage = functools.partial(compute_test_coverage,
                                          client_bugzoo,
                                          snapshot)
        test_to_coverage = executor.map(func_coverage, tests)

    coverage = \
        TestSuiteCoverage({cov.test: cov
                           for cov in test_to_coverage})
    t_running = timer() - t_start
    logger.info("computed coverage (took %.2f seconds)", t_running)
    return coverage


def load_baseline_coverage() -> TestSuiteCoverage:
    """
    Attempts to load coverage information for Baseline A.
    """
    global __BASELINE_COVERAGE
    if __BASELINE_COVERAGE:
        return __BASELINE_COVERAGE

    logger.info("attempting to load precomputed coverage for baseline A.")
    try:
        with open(BASELINE_COVERAGE_FN, 'r') as f:
            jsn = json.load(f)
        coverage = TestSuiteCoverage.from_dict(jsn)
    except Exception:
        logger.exception("failed to load precomputed coverage for baseline A.")
        raise
    # restrict to mutable files
    files = [fn for fn in coverage.lines.files if is_file_mutable(fn)]
    __BASELINE_COVERAGE = coverage.restricted_to_files(files)
    logger.info("loaded precomputed coverage for baseline A.")
    return __BASELINE_COVERAGE


def precompute() -> None:
    """
    Precomputes coverage information for baseline A and saves to a given
    destination.
    """
    dest_fn = 'baseline.coverage.json'
    with bugzoo.server.ephemeral() as client_bugzoo:
        snapshot = fetch_instrumentation_snapshot(client_bugzoo)
        tests = list(snapshot.tests)
        coverage = compute_coverage(client_bugzoo, snapshot, tests)

    logger.info("writing coverage information to disk: %s", dest_fn)
    with open(dest_fn, 'w') as f:
        json.dump(coverage.to_dict(), f, separators=(',',':'))
    logger.info("wrote coverage information to disk.")
