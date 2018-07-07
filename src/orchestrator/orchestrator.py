from typing import List, Tuple, Optional, Callable, Iterator, Dict, Any
from timeit import default_timer as timer
from enum import Enum
import threading
import time
import logging
import datetime
import os
import yaml
import random
import concurrent.futures

import rooibos
import boggart
import bugzoo
import bugzoo.client
import bugzoo.exceptions
import darjeeling
import darjeeling.outcome
import darjeeling.transformation
from bugzoo.exceptions import BugZooException
from bugzoo.core.container import Container
from bugzoo.core.bug import Bug as Snapshot
from bugzoo.core.test import TestCase
from bugzoo.core.spectra import Spectra
from bugzoo.core.fileline import FileLine, FileLineSet
from bugzoo.core.coverage import TestSuiteCoverage, TestCoverage
from bugzoo.util import report_resource_limits, report_system_resources, \
                        indent
from darjeeling.searcher import Searcher
from darjeeling.candidate import Candidate
from boggart import Mutation
from boggart.core.mutant import Mutant
from darjeeling.localization import Localization
from darjeeling.generator import RooibosGenerator
from kaskara import Analysis

from .problem import Problem
from .exceptions import *
from .snapshot import fetch_baseline_snapshot, fetch_instrumentation_snapshot
from .blacklist import is_file_mutable
from .coverage import load_baseline_coverage, compute_mutant_coverage
from .liveness import mutant_fails_test
from .space import build_search_space

logger = logging.getLogger("orchestrator")  # type: logging.Logger
logger.addHandler(logging.NullHandler())

__all__ = ['Orchestrator', 'OrchestratorState', 'OrchestratorOutcome']

# a list of the names of supported mutation operators
OPERATOR_NAMES = [
    'delete-void-function-call',
    'flip-arithmetic-operator',
    'flip-boolean-operator',
    'flip-relational-operator',
    'undo-transformation',
    'delete-conditional-control-flow',
    'flip-signedness'
]


class OrchestratorState(Enum):
    READY_TO_PERTURB = 0
    PERTURBING = 1
    READY_TO_ADAPT = 2
    SEARCHING = 3
    FINISHED = 4
    ERROR = 5


class OrchestratorOutcome(Enum):
    """
    Concisely describes the outcome of the challenge problem.
    """
    COMPLETE_REPAIR = 0
    PARTIAL_REPAIR = 1
    NO_REPAIR = 2


class CandidateEvaluation(object):
    """
    Describes a candidate patch evaluation.
    """
    def __init__(self,
                 patch: darjeeling.candidate.Candidate,
                 outcome: darjeeling.outcome.CandidateOutcome,
                 diff: str
                 ) -> None:
        self.__patch = patch
        self.__outcome = outcome
        self.__diff = diff

    @property
    def diff(self) -> str:
        return self.__diff

    @property
    def tests(self) -> darjeeling.outcome.TestOutcomeSet:
        return self.__outcome.tests

    @property
    def build(self) -> darjeeling.outcome.BuildOutcome:
        return self.__outcome.build


class Orchestrator(object):
    def __init__(self,
                 url_boggart: str,
                 url_bugzoo: str,
                 url_rooibos: str,
                 callback_progress: Callable[[CandidateEvaluation, List[CandidateEvaluation]], None],
                 callback_done: Callable[[List[CandidateEvaluation], int, OrchestratorOutcome, float], None],
                 callback_error: Callable[[str, str], None],
                 threads: int = 8,
                 seed: int = 0
                 ) -> None:
        """
        Constructs a new orchestrator.

        Parameters:
            url_boggart: the base URL of the Hulk server.
            url_bugzoo: the base URL of the BugZoo server.
            callback_status: called when a new patch is added to the pareto
                    front.
            callback_done: called when the search process has finished.
            callback_error: called when an unexpected error is encountered
                during a non-blocking call.
        """
        logger.info("- using BugZoo: %s", bugzoo.__version__)
        logger.info("- using Darjeeling: %s", darjeeling.__version__)
        logger.info("- using boggart: %s", boggart.__version__)
        logger.info("- using RNG seed: %d", seed)
        logger.info("- using %d threads for evaluation", threads)
        report_system_resources(logger)
        report_resource_limits(logger)

        self.__callback_progress = callback_progress
        self.__callback_done = callback_done
        self.__callback_error = callback_error

        # a lock is used to ensure mutually exclusive access to destructive
        # events (i.e., injecting a perturbation or triggering the
        # adaptation).
        self.__lock = threading.Lock()

        # seed the RNG
        self.__seed = seed
        random.seed(seed)

        self.__patches = [] # type: List[CandidateEvaluation]
        self.__state = OrchestratorState.READY_TO_PERTURB
        self.__client_rooibos = rooibos.Client(url_rooibos, timeout_connection=120)
        self.__client_boggart = boggart.Client(url_boggart, timeout_connection=120)
        self.__client_bugzoo = bugzoo.Client(url_bugzoo, timeout_connection=120)
        # TODO it would be nicer if Darjeeling was a service

        self.__num_threads = threads
        self.__problem = None  # type: Optional[Problem]
        self.__searcher = None  # type: Optional[Searcher]
        self.__localization = None  # type: Optional[Localization]
        self.__coverage_for_mutant = None  # type: Optional[TestSuiteCoverage]
        self.__baseline = \
            fetch_baseline_snapshot(self.__client_bugzoo)
        self.__baseline_with_instrumentation = \
            fetch_instrumentation_snapshot(self.__client_bugzoo)

        logger.info("fetching coverage information for Baseline A.")
        self.__coverage_for_baseline = \
            load_baseline_coverage()  # type: TestSuiteCoverage
        logger.debug("mutable files: %s",
                     self.__coverage_for_baseline.lines.files)
        logger.info("fetched coverage information for Baseline A.")
        logger.info("line coverage for Baseline A: %d lines", len(self.lines))

    def shutdown(self) -> None:
        """
        Ensures all resources are safely deallocated.
        """
        if self.__client_boggart:
            logger.info("shutting down boggart")
            try:
                self.__client_boggart.shutdown()
                logger.info("finished shutting down boggart")
            except Exception:
                logger.exception("failed to shutdown boggart")
        else:
            logger.info("skipping boggart cleanup: not connected to boggart")

        if self.__client_bugzoo:
            logger.info("shutting down BugZoo")
            try:
                self.__client_bugzoo.shutdown()
                logger.info("finished shutting down BugZoo")
            except Exception:
                logger.exception("failed to shutdown BugZoo")
        else:
            logger.info("skipping BugZoo cleanup: not connected to BugZoo")

    @property
    def state(self) -> OrchestratorState:
        """
        The current state of the orchestrator.
        """
        return self.__state

    @property
    def boggart(self) -> boggart.Client:
        """
        A connection to the boggart server, used to perturb the system under test.
        """
        return self.__client_boggart

    @property
    def bugzoo(self) -> bugzoo.client.Client:
        """
        A connection to the BugZoo server, used to validate patches and provide
        a sandboxed environment for interacting with (versions of) the system
        under test.
        """
        return self.__client_bugzoo

    @property
    def files(self) -> List[str]:
        """
        A list of the names of the source code files for the original,
        unperturbed system that may be subject to perturbation.
        """
        return self.lines.files

    @property
    def lines(self) -> FileLineSet:
        """
        The set of source code lines in the original, unperturbed system that
        may be subject to perturbation.

        Only lines that are covered by the test suite may be perturbed.
        Furthermore, lines in certain blacklisted files are removed from
        consideration, even if covered by the test suite.
        """
        return self.__coverage_for_baseline.lines

    @property
    def patches(self) -> List[CandidateEvaluation]:
        """
        A list of all of the patches that have been discovered thus far by
        during the search process. If the search process has not begun, an
        empty list is returned.
        """
        return self.__patches.copy()

    @property
    def resource_usage(self) -> Tuple[int, float]:
        """
        A summary of the resources used by the adaptation process, given as a
        tuple `(num_attempts, minutes)`, where `num_attempts` is a count of the
        number of candidate patches that have been evaluated, and `minutes`
        specifies the number of minutes that the search has been running.

        Returns:
            (num_attempts, minutes).
        """
        if self.__searcher:
            num_attempts = self.__searcher.num_candidate_evals
            minutes = self.__searcher.time_running.seconds / 60
        else:
            num_attempts = 0
            minutes = 0.0
        return (num_attempts, minutes)

    # TODO add return type
    def perturbations(self,
                      filename: str,
                      line_num: Optional[int] = None,
                      op_name: Optional[str] = None
                      ):
        """
        Returns a list of all perturbations that can be made to a given file.

        Parameters:
            filename: the path to the file, relative to the root source
                directory.
            line_num: if specified, restricts the list of perturbations to
                those that cover the line with this one-indexed number.
            op_name: if specified, restricts the list of perturbations to
                those that are generated using the mutation operator with
                this name.

        Returns:
            a list of perturbations.

        Raises:
            FileNotFound: if the specified file does not exist or cannot be
                pertubed.
            LineNotFound: if the specified line does not exist or cannot be
                perturbed.
            OperatorNotFound: if no operator with the given name exists.
            AssertionError: if a line number is provided and that line number
                is less than or equal to zero.
        """
        baseline = self.__baseline
        boggartd = self.__client_boggart
        if line_num is None:
            loc_s = filename
        else:
            loc_s = "{}:{}".format(filename, line_num)

        if op_name is None:
            op_s = "all operators"
        else:
            op_s = "operator: {}".format(op_name)

        logger.info("Finding all perturbations in %s using %s.", loc_s, op_s)
        assert line_num is None or line_num > 0

        line = FileLine(filename, line_num) if line_num else None

        if filename not in self.files:
            raise FileNotFound(filename)
        if line is not None and line not in self.lines:
            raise LineNotFound(line)
        if op_name is not None and op_name not in OPERATOR_NAMES:
            raise OperatorNotFound(op_name)

        # fetch the mutation operators
        if op_name is not None:
            operators = [boggartd.operators[op_name]]
        else:
            operators = [boggartd.operators[name] for name in OPERATOR_NAMES]
        logger.debug("Using perturbation operators: %s",
                     [op.name for op in operators])

        if line_num is None:
            restrict_to_lines = [l.num for l in self.lines[filename]]
        else:
            restrict_to_lines = [line_num]
        logger.info("Looking for perturbations at lines: %s",
                    restrict_to_lines)

        generator_mutations = \
            boggartd.mutations(baseline,
                               filepath=filename,
                               operators=operators,
                               restrict_to_lines=restrict_to_lines)
        mutations = list(generator_mutations)
        logger.info("Found %d perturbations in %s using %s.",
                    len(mutations), loc_s, op_s)

        return mutations

    def _patch_to_evaluation(self, patch: Candidate) -> CandidateEvaluation:
        """
        Transforms a Darjeeling patch data structure into its Orchestrator
        equivalent.
        """
        return CandidateEvaluation(patch,
                                   self.__searcher.outcomes[patch],
                                   str(patch.to_diff(self.__problem)))

    def _build_problem(self, perturbation: Mutation) -> Problem:
        """
        Transforms the scenario into a repair problem.

        Raises:
            FailedToComputeCoverage: if an error occurred during the coverage
                computing process.
        """
        try:
            snapshot = self.__client_bugzoo.bugs[perturbation.snapshot]
            self.__coverage_for_mutant = \
                compute_mutant_coverage(self.__client_bugzoo,
                                        self.__client_boggart,
                                        perturbation)
            covered_files = self.__coverage_for_mutant.failing.lines.files
            analysis = \
                Analysis.build(self.__client_bugzoo, snapshot, covered_files)
            problem = \
                Problem(self.__client_bugzoo,
                        self.__client_rooibos,
                        self.__coverage_for_mutant,
                        perturbation,
                        analysis)
            self.__localization = self._compute_localization(problem)
        except Exception:
            self.__localization = None
            self.__coverage_for_mutant = None
            logger.exception("Failed to transform perturbed code into a repair problem: encountered unexpected error whilst generating coverage.")  # noqa: pycodestyle
            raise FailedToComputeCoverage
        return problem

    def perturb(self, perturbation: Mutation) -> None:
        """
        Attempts to generate baseline B by perturbing the original system.

        Parameters:
            perturbation: the perturbation that should be applied.

        Raises:
            NotReadyToPerturb: if the system is not ready to be perturbed.
            NeutralPerturbation: if the given mutant does not fail any tests.
            FailedToComputeCoverage: if coverage information could not be
                obtained for the given mutant.
        """
        logger.info("Attempting to perturb system using mutation: %s",
                    perturbation)
        bz = self.__client_bugzoo
        boggartd = self.__client_boggart
        baseline = self.__baseline
        with self.__lock:
            if self.state != OrchestratorState.READY_TO_PERTURB:
                logger.warning("System is not ready to be perturbed [state: %s]",  # noqa: pycodestyle
                               str(self.state))
                raise NotReadyToPerturb

            mutant = None
            self.__state = OrchestratorState.PERTURBING
            try:
                try:
                    # TODO capture unexpected errors during snapshot creation
                    logger.info("Applying perturbation to baseline snapshot.")
                    mutant = boggartd.mutate(baseline, [perturbation])
                    snapshot = bz.bugs[mutant.snapshot]
                    logger.info("Generated mutant snapshot: %s", snapshot)
                    if not mutant_fails_test(bz, boggartd, mutant):
                        raise NeutralPerturbation
                    self.__problem = self._build_problem(mutant)
                    self.__state = OrchestratorState.READY_TO_ADAPT
                    logger.info("Transformed perturbed code into a repair problem.")  # noqa: pycodestyle
                except OrchestratorError:
                    raise NeutralPerturbation
                except Exception as e:
                    raise UnexpectedError(e)
            except OrchestratorError:
                # deregister the mutant
                if mutant:
                    logger.debug("destroying mutant for perturbation.")
                    try:
                        del boggartd.mutants[mutant.uuid]
                    except Exception:
                        logger.exception("failed to destroy mutant for perturbation")
                        raise
                    logger.debug("destroyed mutant for perturbation")

                logger.debug("Resetting system state to be ready to perturb.")
                self.__problem = None
                self.__state = OrchestratorState.READY_TO_PERTURB
                logger.debug("System is now ready to perturb.")
                raise
        logger.info("Successfully perturbed system using mutation: %s",
                    perturbation)

    def _compute_localization(self, problem: Problem) -> Localization:
        """
        Computes the fault localization for baseline C.

        Raises:
            FailedToComputeCoverage: if no lines are implicated by the fault
                localization.
        """
        def suspiciousness(ep: int, np: int, ef: int, nf: int) -> float:
            # FIXME greedy!
            # return 1.0 if nf == 0 and ep == 0 else 0.0
            if nf != 0:
                return 0.0
            return np / (ep + np + 1)
            # return 1.0 if nf == 0 else 0.0
        logger.info("computing fault localization")
        logger.debug("passing coverage:\n%s", problem.coverage.passing)
        logger.debug("failing coverage:\n%s", problem.coverage.failing)
        # logger.info("spectra:\n%s", Spectra.from_coverage(problem.coverage))
        # FIXME this should be independent of Problem
        try:
            localization = Localization.build(problem, suspiciousness)
        except darjeeling.exceptions.NoImplicatedLines:
            raise FailedToComputeCoverage
        logger.info("computed fault localization (%d files, %d lines):\n%s",
                    len(localization.files), len(localization), localization)
        lines = FileLineSet.from_list([l for l in localization])
        logger.info("suspicious lines:\n%s", indent(repr(lines), 2))
        return localization

    def adapt(self,
              *,
              minutes: Optional[float] = None,
              attempts: Optional[int] = None
              ) -> None:
        """
        Attempts to trigger the code adaptation process.

        Parameters:
            minutes: an optional time limit on the search process, given in
                minutes.
            attempts: an optional limit on the number of candidate patches
                that the search may evaluate before terminating.

        Raises:
            NotReadyToAdapt: if the system is not ready to be adapted or if
                adaptation has already begun.
            AssertionError: if a non-positive time limit or number of attempts
                is provided.
            NoSearchLimits: if neither a time or candidate limit is provided.
        """
        logger.info("triggering adaptation")
        assert minutes is None or minutes > 0
        assert attempts is None or attempts > 0

        if minutes is None and attempts is None:
            logger.error("no resource limits specified")
            raise NoSearchLimits

        time_limit = datetime.timedelta(minutes=minutes) if minutes else None

        with self.__lock:
            if self.state != OrchestratorState.READY_TO_ADAPT:
                logger.error("unable to trigger adaptation: system is not ready to adapt [state: %s]",  # noqa: pycodestyle
                             self.state)
                raise NotReadyToAdapt

            self.__state = OrchestratorState.SEARCHING
            logger.debug("set orchestrator state to %s", self.__state)

            # start the search on a separate thread
            def search():
                bz = self.__client_bugzoo
                try:
                    problem = self.__problem
                    assert self.__localization is not None
                    candidates = build_search_space(problem,
                                                    self.__localization)
                    logger.debug("constructing search mechanism")
                    self.__searcher = Searcher(bugzoo=self.__client_bugzoo,
                                               problem=problem,
                                               candidates=candidates,
                                               threads=self.__num_threads,
                                               candidate_limit=attempts,
                                               time_limit=time_limit)
                    logger.debug("constructed search mechanism")
                    logger.info("beginning search")
                    for patch in self.__searcher:
                        evaluation = self._patch_to_evaluation(patch)
                        self.__patches.append(evaluation)
                        self.__callback_progress(evaluation, self.patches)
                    logger.info("finished search")
                    log = [self._patch_to_evaluation(p)
                           for p in self.__searcher.history]
                    self.__state = OrchestratorState.FINISHED
                    if self.patches:
                        outcome = OrchestratorOutcome.COMPLETE_REPAIR
                    else:
                        outcome = OrchestratorOutcome.NO_REPAIR
                    num_attempts, runtime = self.resource_usage
                    self.__callback_done(log, num_attempts, outcome, self.patches, runtime)

                # FIXME handle unexpected errors
                except Exception as err:
                    logger.exception("an unexpected error occurred during adaptation: %s",  # noqa: pycodestyle
                                     err)
                    self.__state = OrchestratorState.ERROR
                    kind = err.__class__.__name__
                    self.__callback_error(kind, str(err))

            # TODO ensure that thread is killed cleanly
            logger.debug("creating search thread")
            thread = threading.Thread(target=search)
            logger.debug("starting search thread")
            thread.start()
            logger.info("finished triggered adaptation")
