from typing import List, Tuple, Optional, Callable
from enum import Enum
import threading
import time
import logging

import hulk
import bugzoo
import bugzoo.client
import darjeeling
import darjeeling.outcome
from bugzoo.core.fileline import FileLine, FileLineSet
from darjeeling.problem import Problem
from darjeeling.searcher import Searcher
from darjeeling.candidate import Candidate

__ALL__ = ['Orchestrator', 'OrchestratorState', 'OrchestratorOutcome']

BASE_IMAGE_NAME = 'mars:base'

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
                 outcome: darjeeling.outcome.CandidateOutcome
                 ) -> None:
        self.__patch = patch
        self.__outcome = outcome

    @property
    def diff(self) -> str:
        return "NOT YET IMPLEMENTED"

    @property
    def tests(self) -> darjeeling.outcome.TestOutcomeSet:
        return self.__outcome.tests

    @property
    def build(self) -> darjeeling.outcome.BuildOutcome:
        return self.__outcome.build


class Orchestrator(object):
    def __init__(self,
                 url_hulk: str,
                 url_bugzoo: str,
                 callback_progress: Callable[[CandidateEvaluation, List[CandidateEvaluation]], None],
                 callback_done: Callable[[List[CandidateEvaluation], int, OrchestratorOutcome, float], None],
                 callback_error: Callable[[str, str], None]
                 ) -> None:
        """
        Constructs a new orchestrator.

        Parameters:
            url_hulk: the base URL of the Hulk server.
            url_bugzoo: the base URL of the BugZoo server.
            callback_status: called when a new patch is added to the pareto
                front.
            callback_done: called when the search process has finished.
            callback_error: called when an unexpected error is encountered
                during a non-blocking call.
        """
        self.__logger = logging.getLogger('orchestrator')
        self.__logger.info("- using BugZoo: {}".format(bugzoo.__version__))
        self.__logger.info("- using Darjeeling: {}".format(darjeeling.__version__))

        self.__callback_progress = callback_progress
        self.__callback_done = callback_done
        self.__callback_error = callback_error

        # a lock is used to ensure mutually exclusive access to destructive
        # events (i.e., injecting a perturbation or triggering the
        # adaptation).
        self.__lock = threading.Lock()

        self.__patches = [] # type: List[CandidateEvaluation]
        self.__state = OrchestratorState.READY_TO_PERTURB
        self.__client_hulk = hulk.Client(url_hulk, timeout_connection=120)
        self.__client_bugzoo = bugzoo.Client(url_bugzoo, timeout_connection=120)
        # TODO it would be nicer if Darjeeling was a service

        self.__problem = None # type: Optional[Problem]
        self.__searcher = None # type: Optional[Searcher]

        # compute and cache coverage information for the original system
        self.__baseline = self.__client_bugzoo.bugs["mars:base"]

    @property
    def logger(self) -> logging.Logger:
        """
        The associated with this orchestrator.
        """
        return self.__logger

    @property
    def state(self) -> OrchestratorState:
        """
        The current state of the orchestrator.
        """
        return self.__state

    @property
    def baseline(self) -> bugzoo.core.bug.Bug:
        """
        The BugZoo snapshot for the original version of the system under test.
        """
        return self.__baseline

    @property
    def hulk(self) -> hulk.Client:
        """
        A connection to the Hulk server, used to perturb the system under test.
        """
        return self.__client_hulk

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

        return True

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
        coverage = self.bugzoo.bugs.coverage(self.baseline)
        lines = coverage.lines

        # restrict to files that may be mutated
        files = [fn for fn in lines.files if self.__is_file_mutable(fn)]
        lines = lines.restricted_to_files(files)

        return lines

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
            operators = [self.hulk.operators[op_name]]
        else:
            operators = [self.hulk.operators[name] for name in OPERATOR_NAMES]

        mutations = hulk_client.mutations(self.baseline,
                                          filename=filename,
                                          operators=operators,
                                          line=line)
        return mutations

    # TODO add arg type
    def perturb(self, perturbation) -> None:
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
        with self.__lock:
            if self.state != OrchestratorState.READY_TO_PERTURB:
                raise NotReadyToPerturb()

            self.__state = OrchestratorState.PERTURBING
            try:
                # TODO capture unexpected errors during snapshot creation
                snapshot = self.hulk.mutate(self.baseline, perturbation)
                # TODO pass logger
                try:
                    self.__problem = Problem(bz=self.bugzoo,
                                             bug=snapshot,
                                             cache_coverage=False)
                    self.__state = OrchestratorState.READY_TO_ADAPT
                except darjeeling.exceptions.NoFailingTests:
                    raise NeutralPerturbation()
                except darjeeling.exceptions.NoImplicatedLines:
                    raise FailedToComputeCoverage()

            except:
                self.__problem = None
                self.__state = OrchestratorState.READY_TO_PERTURB
                raise

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
        assert minutes is None or minutes > 0
        assert attempts is None or attempts > 0

        if minutes is None and attempts is None:
            raise NoSearchLimits()

        time_limit = datetime.timedelta(minutes=minutes) if minutes else None

        with self.__lock:
            if self.state != OrchestratorState.READY_TO_ADAPT:
                raise NotReadyToAdapt()

            self.__state = OrchestratorState.SEARCHING

            # start the search on a separate thread
            def search():
                try:
                    problem = self.__problem
                    candidates = \
                        darjeeling.generator.SampleByLocalization(problem=problem,
                                                                  localization=problem.localization,
                                                                  snippets=problem.snippets)
                    self.__searcher = Searcher(bugzoo=self.bugzoo,
                                               problem=problem,
                                               candidate=candidates,
                                               num_candidates=attempts,
                                               time_limit=time_limit)
                    for patch in self.__searcher:
                        outcome = self.__searcher.outcomes[patch]
                        evaluation = CandidateEvaluation(patch, outcome)
                        self.__patches.append(evaluation)
                        self.__callback_progress(evaluation, self.patches)

                    # FIXME extract log of attempted patches from darjeeling
                    log = []
                    self.__state = OrchestratorState.FINISHED
                    if self.patches:
                        outcome = OrchestratorOutcome.COMPLETE_REPAIR
                    else:
                        outcome = OrchestratorOutcome.NO_REPAIR
                    self.__callback_done(log, num_attempts, outcome, self.patches, runtime)

                # FIXME handle unexpected errors
                except Exception as err:
                    self.__state = OrchestratorState.ERROR
                    kind = err.__class__.name
                    self.__callback_error(kind, str(err))

            # TODO ensure that thread is killed cleanly
            thread = threading.Thread(target=search)
            thread.start()
