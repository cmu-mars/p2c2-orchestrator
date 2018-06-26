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

from .orchestrator import fetch_instrumentation_snapshot
from .exceptions import FailedToComputeCoverage
from .blacklist import is_file_mutable

logger = logging.getLogger(__name__)  # type: logging.Logger
logger.setLevel(logging.DEBUG)

BASELINE_COVERAGE_FN = \
    os.path.join(os.path.dirname(__file__),
                 'data/baseline.coverage.json')  # type: str


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
        logger.exception("failed to compute coverage for mutant (%s) on test (%s).",
                         mutant.uuid, test.name)
        raise FailedToComputeCoverage
    finally:
        if container is not None:
            del ctr_mgr[container.uid]


def compute_mutant_coverage() -> None:
    # FIXME restrict to tests that cover the perturbed file
    tests = list(self.__baseline.tests)

    logger.info("computing coverage for mutant: %s", mutant)
    logger.info("creating temporary instrumented mutant")
    mutant_instrumented = None

    try:
        mutant_instrumented = \
            bgrt.mutate(self.__baseline_with_instrumentation,
                        mutant.mutations)
        logger.info("created temporary instrumented mutant: %s",
                    mutant_instrumented)
    except Exception:
        logger.warning("failed to compute coverage for mutant: %s",
                       mutant)
        raise FailedToComputeCoverage

    finally:
        if mutant_instrumented:
            del bgrt.mutants[mutant_instrumented.uuid]


def compute_coverage(client_bugzoo: BugZooClient,
                     snapshot: Snapshot,
                     tests: List[TestCase],
                     *,
                     threads: int = 4
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
    logger.debug("computed coverage (took %.2f seconds)", t_running)
    return coverage


def load_baseline_coverage() -> TestSuiteCoverage:
    """
    Attempts to load coverage information for Baseline A.
    """
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
    coverage = coverage.restricted_to_files(files)
    logger.info("loaded precomputed coverage for baseline A.")
    return coverage


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
