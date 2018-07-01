"""
This module is used to check the liveness of a perturbation.
"""
import logging

from bugzoo.client import Client as BugZooClient
from boggart import Client as BoggartClient
from boggart.core import Mutant
from boggart.core.location import FileLine

from .coverage import load_baseline_coverage

logger = logging.getLogger(__name__)  # type: logging.Logger
logger.setLevel(logging.DEBUG)


def mutant_fails_test(client_bugzoo: BugZooClient,
                      client_boggart: BoggartClient,
                      mutant: Mutant
                      ) -> None:
    """
    Determines whether a given mutant is killed by the test suite.
    """
    logger.info("ensuring that mutant fails at least one test")
    mgr_ctr = client_bugzoo.containers
    snapshot = client_bugzoo.bugs[mutant.snapshot]

    # find the set of lines changed by the mutant
    replacements = client_boggart.mutations_to_replacements(snapshot,
                                                            mutant.mutations)
    locations = [r.location for r in replacements]
    lines = [FileLine(l.filename, l.start.line) for l in locations]
    logger.info("lines changed by mutant: %s", lines)

    # restrict to the test outcomes that may be changed by the mutant
    coverage = load_baseline_coverage()
    # logger.info("coverage tests: %s", [t for t in coverage])
    tests = list(snapshot.tests)
    tests = [t for t in tests \
             if any(line in coverage[t.name] for line in lines)]
    logger.info("tests covered by mutant: %s", [t.name for t in tests])

    container = None
    try:
        killed = False
        container = mgr_ctr.provision(snapshot)
        for test in tests:
            outcome = mgr_ctr.test(container, test)
            if not outcome.passed:
                logger.info("mutant killed by test: %s", test.name)
                killed = True
                break
        if not killed:
            logger.info("mutant was not killed by any of the test cases")
            return False
    finally:
        if container is not None:
            del mgr_ctr[container.uid]

    logger.info("verified that mutant fails at least one test")
    return True
