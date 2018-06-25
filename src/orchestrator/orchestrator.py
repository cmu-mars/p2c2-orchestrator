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
from bugzoo.core.fileline import FileLine, FileLineSet
from bugzoo.core.coverage import TestSuiteCoverage, TestCoverage
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


def _load_manifest() -> Dict[str, Any]:
    fn = os.path.join(os.path.dirname(__file__), 'baseline.yml')
    with open(fn, 'r') as f:
        desc = yaml.load(f)

    # add oracle information
    tests = desc['test-harness']['tests']
    for test in tests:
        has_oracle = 'oracle' in test
        if 'kind' in test and test['kind'] != 'system' and not has_oracle:
            test['oracle'] = {'contains': "[  PASSED  ]"}
    desc['source'] = None
    return desc


def fetch_baseline_snapshot(bz: bugzoo.client.Client) -> Snapshot:
    desc = _load_manifest()
    snapshot = Snapshot.from_dict(desc)
    bz.bugs.register(snapshot)
    return snapshot


def fetch_instrumentation_snapshot(bz: bugzoo.client.Client) -> Snapshot:
    desc = _load_manifest()
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

    def shutdown(self) -> None:
        """
        Ensures all resources are safely deallocated.
        """
        if self.__client_bugzoo:
            logger.info("destroying all BugZoo containers")
            try:
                self.__client_bugzoo.containers.clear()
                logger.info("destroyed all BugZoo containers")
            except Exception:
                logger.exception("failed to destroy BugZoo containers")
        else:
            logger.info("skipping BugZoo cleanup: not connected to BugZoo")

        if self.__client_boggart:
            logger.info("destroying all boggart mutants")
            try:
                self.__client_boggart.mutants.clear()
                logger.info("destroyed all boggart mutants")
            except Exception:
                logger.exception("failed to destroy boggart mutants")
        else:
            logger.info("skipping boggart cleanup: not connected to boggart")

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
            # FIXME keep or chuck?
            tests = [snapshot.harness['t1']]
            for test in tests:
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

        # FIXME stop provisioning containers -- have one for each thread
        def get_test_coverage(mutant: Mutant, test: TestCase) -> TestCoverage:
            logger.info("getting coverage for test: %s", test.name)
            container = None
            try:
                snapshot = bgz.bugs[mutant.snapshot]
                container = bgz.containers.provision(snapshot)
                # logger.info("using container (%s) to generate coverage for test (%s)",
                #             container.uid, test.name)
                outcome = bgz.containers.test(container, test)
                lines = bgz.containers.extract_coverage(container)
                # logger.info("generated coverage for test (%s):\n%s",
                #             test.name, lines)
                return TestCoverage(test.name, outcome, lines)
            except BugZooException:
                logger.exception("failed to compute coverage for mutant (%s) on test (%s).",
                                 mutant.uuid, test.name)
                raise FailedToComputeCoverage
            finally:
                if container is not None:
                    del bgz.containers[container.uid]

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

            # FIXME compute coverage
            # thread pool?
            logger.debug("computing coverage")
            t_start = timer()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:  # FIXME parameterise
                test_to_coverage = \
                    executor.map(lambda t: (t, get_test_coverage(mutant_instrumented, t)),
                                 tests)
            coverage = \
                TestSuiteCoverage({t.name: cov
                                   for (t, cov) in test_to_coverage})
            t_running = timer() - t_start
            logger.debug("computed coverage (took %.2f seconds)", t_running)

        except Exception:
            logger.warning("failed to compute coverage for mutant: %s",
                           mutant)
            raise FailedToComputeCoverage

        finally:
            if mutant_instrumented:
                del bgrt.mutants[mutant_instrumented.uuid]

        logger.debug("restricting coverage to mutable files")
        coverage = coverage.restricted_to_files(self.files)
        logger.debug("restricted coverage to mutable files")
        logger.info("computed coverage for mutant: %s", mutant)
        return coverage

    def _build_problem(self, perturbation: Mutation) -> Problem:
        """
        Transforms the scenario into a repair problem.

        Raises:
            FailedToComputeCoverage: if an error occurred during the coverage
                computing process.
        """
        try:
            self.__coverage_for_mutant = self._compute_coverage(perturbation)
            problem = \
                Problem(self.__client_bugzoo,
                        self.__client_rooibos,
                        self.__coverage_for_mutant,
                        perturbation)
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
                raise NotReadyToPerturb()

            mutant = None
            self.__state = OrchestratorState.PERTURBING
            try:
                try:
                    # TODO capture unexpected errors during snapshot creation
                    logger.info("Applying perturbation to baseline snapshot.")
                    mutant = boggartd.mutate(baseline, [perturbation])
                    snapshot = bz.bugs[mutant.snapshot]
                    logger.info("Generated mutant snapshot: %s", snapshot)
                    self._check_liveness(mutant)
                    self.__problem = self._build_problem(mutant)
                    self.__state = OrchestratorState.READY_TO_ADAPT
                    logger.info("Transformed perturbed code into a repair problem.")  # noqa: pycodestyle
                except OrchestratorError:
                    raise
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
            return 1.0 if nf == 0 else 0.0
        logger.info("computing fault localization")
        try:
            localization = Localization.build(problem, suspiciousness)
        except darjeeling.exceptions.NoImplicatedLines:
            raise FailedToComputeCoverage
        logger.info("computed fault localization (%d files, %d lines)",
                    len(localization.files), len(localization))
        return localization

    def _construct_search_space(self,
                                problem: Problem,
                                localization: Localization
                                ) -> Iterator[Candidate]:
        """
        Used to compose the sequence of patches that should be attempted.
        """
        schemas = [
            # boolean operators
            darjeeling.transformation.AndToOr,
            darjeeling.transformation.OrToAnd,
            # relation operators
            darjeeling.transformation.LEToGT,
            darjeeling.transformation.GTToLE,
            darjeeling.transformation.GEToLT,
            darjeeling.transformation.LTToGE,
            darjeeling.transformation.EQToNEQ,
            darjeeling.transformation.NEQToEQ,
            # arithmetic operators
            darjeeling.transformation.PlusToMinus,
            darjeeling.transformation.MinusToPlus,
            darjeeling.transformation.MulToDiv,
            darjeeling.transformation.DivToMul,
            darjeeling.transformation.SignedToUnsigned,
            # insert void function call
            # darjeeling.transformation.InsertVoidFunctionCall,
            # insert conditional control flow
            # darjeeling.transformation.InsertConditionalReturn,
            # darjeeling.transformation.InsertConditionalBreak,
            # apply transformation
            #darjeeling.transformation.ApplyTransformation
        ]
        logger.info("constructing search space")
        transformations = RooibosGenerator(problem,
                                           localization,
                                           schemas)
        candidates = \
            darjeeling.generator.SingleEditPatches(transformations)
        logger.info("constructed search space")
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
                raise NotReadyToAdapt

            self.__state = OrchestratorState.SEARCHING
            logger.debug("set orchestrator state to %s", self.__state)

            # start the search on a separate thread
            def search():
                bz = self.__client_bugzoo
                try:
                    problem = self.__problem
                    assert self.__localization is not None
                    candidates = \
                        self._construct_search_space(problem,
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
