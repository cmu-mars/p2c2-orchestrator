from typing import List, Tuple, Optional, Callable, Iterator
from timeit import default_timer as timer
from enum import Enum
import threading
import time
import logging
import datetime
import os
import yaml

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
from bugzoo.core.fileline import FileLine, FileLineSet
from bugzoo.core.coverage import TestSuiteCoverage
from darjeeling.searcher import Searcher
from darjeeling.candidate import Candidate
from boggart import Mutation
from boggart.core.mutant import Mutant
from darjeeling.localization import Localization
from darjeeling.generator import RooibosGenerator

from .problem import Problem
from .exceptions import *

logger = logging.getLogger("orchestrator")  # type: logging.Logger
logger.addHandler(logging.NullHandler())

__all__ = ['Orchestrator', 'OrchestratorState', 'OrchestratorOutcome']

BASE_IMAGE_NAME = 'mars:base'
__BASELINE_SNAPSHOT = None  # type: Optional[Snapshot]
__INSTRUMENTATION_SNAPSHOT = None  # type: Optional[Snapshot]

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


def fetch_baseline_snapshot(bz: bugzoo.client.Client) -> Snapshot:
    fn = os.path.join(os.path.dirname(__file__), 'baseline.yml')
    with open(fn, 'r') as f:
        desc = yaml.load(f)
    desc['source'] = None
    snapshot = Snapshot.from_dict(desc)
    bz.bugs.register(snapshot)
    return snapshot


def fetch_instrumentation_snapshot(bz: bugzoo.client.Client) -> Snapshot:
    fn = os.path.join(os.path.dirname(__file__), 'baseline.yml')
    with open(fn, 'r') as f:
        desc = yaml.load(f)
    desc['source'] = None
    desc['name'] = 'mars:instrument'
    desc['image'] = 'cmumars/cp2:instrument'
    snapshot = Snapshot.from_dict(desc)
    bz.bugs.register(snapshot)
    return snapshot


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
                 callback_error: Callable[[str, str], None]
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
        logger.info("- using BugZoo: {}".format(bugzoo.__version__))
        logger.info("- using Darjeeling: {}".format(darjeeling.__version__))
        logger.info("- using boggart: {}".format(boggart.__version__))

        self.__callback_progress = callback_progress
        self.__callback_done = callback_done
        self.__callback_error = callback_error

        # a lock is used to ensure mutually exclusive access to destructive
        # events (i.e., injecting a perturbation or triggering the
        # adaptation).
        self.__lock = threading.Lock()

        self.__patches = [] # type: List[CandidateEvaluation]
        self.__state = OrchestratorState.READY_TO_PERTURB
        self.__client_rooibos = rooibos.Client(url_rooibos, timeout_connection=120)
        self.__client_boggart = boggart.Client(url_boggart, timeout_connection=120)
        self.__client_bugzoo = bugzoo.Client(url_bugzoo, timeout_connection=120)
        # TODO it would be nicer if Darjeeling was a service

        self.__problem = None  # type: Optional[Problem]
        self.__searcher = None  # type: Optional[Searcher]
        self.__coverage_for_mutant = None  # type: Optional[TestSuiteCoverage]
        self.__coverage_for_baseline = None  # type: Optional[TestSuiteCoverage]
        self.__baseline = \
            fetch_baseline_snapshot(self.__client_bugzoo)
        self.__baseline_with_instrumentation = \
            fetch_instrumentation_snapshot(self.__client_bugzoo)

        logger.info("fetching coverage information for Baseline A.")
        self.__coverage_for_baseline = \
            self.__client_bugzoo.bugs.coverage(self.__baseline)
        logger.debug("restricting to mutable files")
        lines = self.__coverage_for_baseline.lines
        files = [fn for fn in lines.files if self.__is_file_mutable(fn)]
        logger.debug("mutable files: %s", files)
        self.__coverage_for_baseline = \
            self.__coverage_for_baseline.restricted_to_files(files)
        logger.info("fetched coverage information for Baseline A.")
        logger.info("line coverage for Baseline A: %d lines", len(self.lines))

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

    def __is_file_mutable(self, fn: str) -> bool:
        """
        Determines whether a given source code file may be the subject of
        perturbation on the basis of its name.
        """
        if not fn.endswith('.cpp'):
            return False

        #
        # TODO: ignore blacklisted files (e.g., Gazebo, ROS core code)
        #
        blacklist = [
            'src/yujin_ocs/yocs_cmd_vel_mux/src/cmd_vel_subscribers.cpp',
            'src/rospack/src/rospack.cpp',
            'src/ros_comm/xmlrpcpp/src/XmlRpcUtil.cpp'
        ]
        return fn not in blacklist

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

        restrict_to_lines = None if line_num is None else [line_num]
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

    def _check_liveness(self, mutant: Mutant) -> None:
        """
        Determines whether a given mutant is killed by the test suite.
        """
        mgr_ctr = self.__client_bugzoo.containers
        snapshot = self.__client_bugzoo.bugs[mutant.snapshot]
        logger.info("Ensuring that mutant fails at least one test")
        try:
            killed = False
            container = mgr_ctr.provision(snapshot)
            for test in snapshot.tests:
                outcome = mgr_ctr.test(container, test)
                if not outcome.passed:
                    killed = True
                    break
            if not killed:
                logger.info("Mutant was not killed by any of the test cases")
                raise NeutralPerturbation
        finally:
            del mgr_ctr[container.uid]
        logger.info("Verified that mutant fails at least one test")

    def _compute_coverage(self, mutant: Mutant) -> TestSuiteCoverage:
        """
        Attempts to compute coverage information for a given mutant.
        """
        bgrt = self.__client_boggart
        bgz = self.__client_bugzoo

        logger.debug("computing coverage for mutant: %s", mutant)
        logger.debug("generating diff for mutant: %s", mutant)
        diff = bgrt.mutations_to_diff(self.__baseline, mutant.mutations)
        logger.debug("generated diff for mutant: %s", mutant)
        container = None
        try:
            container = bgz.containers.provision(self.__baseline_with_instrumentation)
            logger.debug("applying diff to instrumented container")
            bgz.containers.patch(container, diff)
            logger.debug("rebuilding program")
            t_start = timer()
            bgz.containers.exec(container,
                                'catkin build',
                                context='/ros_ws')
            t_running = timer() - t_start
            logger.debug("rebuilt program (took %.2f seconds)", t_running)
            logger.debug("computing coverage")
            coverage = bgz.containers.coverage(container, instrument=False)
            logger.debug("computed coverage")
        except BugZooException:
            raise FailedToComputeCoverage
        finally:
            if container is not None:
                del bgz.containers[container.uid]
        logger.debug("restricting coverage to mutable files")
        coverage = coverage.restricted_to_files(self.files)
        logger.debug("restricted coverage to mutable files")
        logger.debug("computed coverage for mutant: %s", mutant)
        return coverage

    def _build_problem(self, perturbation: Mutation) -> Problem:
        """
        Transforms the scenario into a repair problem.

        Raises:
            FailedToComputeCoverage: if an error occurred during the coverage
                computing process.
        """
        coverage = self._compute_coverage(perturbation)
        logger.info("Transformed perturbed code into a repair problem.")  # noqa: pycodestyle
        try:
            self.__problem = \
                Problem(self.__client_bugzoo,
                        self.__client_rooibos,
                        coverage,
                        perturbation)
            self.__state = OrchestratorState.READY_TO_ADAPT
        except darjeeling.exceptions.NoImplicatedLines:  # noqa: pycodestyle
            logger.exception("Failed to transform perturbed code into a repair problem: encountered unexpected error whilst generating coverage.")  # noqa: pycodestyle
            raise FailedToComputeCoverage

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
                raise NotReadyToPerturb()

            self.__state = OrchestratorState.PERTURBING
            try:
                # TODO capture unexpected errors during snapshot creation
                logger.info("Applying perturbation to baseline snapshot.")
                mutant = boggartd.mutate(baseline, [perturbation])
                snapshot = bz.bugs[mutant.snapshot]
                logger.info("Generated mutant snapshot: %s", snapshot)
                self._check_liveness(mutant)
                self._build_problem(mutant)
            except OrchestratorError:
                logger.debug("Resetting system state to be ready to perturb.")
                self.__problem = None
                self.__state = OrchestratorState.READY_TO_PERTURB
                logger.debug("System is now ready to perturb.")
                raise
        logger.info("Successfully perturbed system using mutation: %s",
                    perturbation)

    def _compute_localization(self) -> Localization:
        def suspiciousness(ep: int, np: int, ef: int, nf: int) -> float:
            return 1.0 if nf == 0 else 0.0
        logger.info("computing fault localization")
        localization = Localization.build(self.__problem, suspiciousness)
        logger.info("computed fault localization (%d files, %d lines)",
                    len(localization.files), len(localization))
        return localization

    def _construct_search_space(self) -> Iterator[Candidate]:
        """
        Used to compose the sequence of patches that should be attempted.
        """
        logger.debug("constructing search space")
        localization = self._compute_localization()
        schemas = [
            darjeeling.transformation.AndToOr,
            darjeeling.transformation.OrToAnd,
            darjeeling.transformation.LEToGT,
            darjeeling.transformation.GTToLE
        ]
        transformations = RooibosGenerator(self.__problem,
                                           localization,
                                           schemas)
        candidates = \
            darjeeling.generator.SingleEditPatches(transformations)
        logger.debug("constructed search space")
        return candidates

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
                raise NotReadyToAdapt()

            self.__state = OrchestratorState.SEARCHING
            logger.debug("set orchestrator state to %s", self.__state)

            # start the search on a separate thread
            def search():
                bz = self.__client_bugzoo
                try:
                    problem = self.__problem
                    candidates = self._construct_search_space()
                    logger.debug("constructing search mechanism")
                    self.__searcher = Searcher(bugzoo=self.__client_bugzoo,
                                               problem=problem,
                                               candidates=candidates,
                                               # num_candidates=attempts,
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
