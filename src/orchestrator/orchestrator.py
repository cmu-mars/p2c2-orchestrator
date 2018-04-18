from typing import List
import threading
import time

import hulk
import bugzoo
import darjeeling
from darjeeling.problem import Problem
from darjeeling.searcher import Searcher


__ALL__ = ['Orchestrator', 'OrchestratorState']


class OrchestratorState(Enum):
    READY_TO_PERTURB = auto()
    PERTURBING = auto()
    READY_TO_ADAPT = auto()
    SEARCHING = auto()
    FINISHED = auto()


class Orchestrator(object):
    def __init__(self,
                 url_hulk: str,
                 url_bugzoo: str
                 ) -> None:
        """
        Constructs a new orchestrator.

        Parameters:
            url_hulk: the base URL of the Hulk server.
            url_bugzoo: the base URL of the BugZoo server.
        """
        # a lock is used to ensure mutually exclusive access to destructive
        # events (i.e., injecting a perturbation or triggering the
        # adaptation).
        self.__lock = threading.Lock()

        self.__event_finished = threading.Event()
        self.__patches = [] # type: List[darjeeling.candidate.Candidate]

        self.__state = OrchestratorState.READY_TO_PERTURB
        self.__client_hulk = hulk.Client(url_hulk)
        self.__client_bugzoo = bugzoo.Client(url_bugzoo)
        # TODO it would be nicer if Darjeeling was a service

        self.__problem = None # type: Optional[Problem]
        self.__searcher = None # type: Optional[Searcher]

        # FIXME wait for servers to be ready
        time.sleep(30)

        # compute and cache coverage information for the original system
        self.__baseline = self.__client_bugzoo.bugs["mars:baseline"]

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
    def bugzoo(self) -> bugzoo.Client:
        """
        A connection to the BugZoo server, used to validate patches and provide
        a sandboxed environment for interacting with (versions of) the system
        under test.
        """
        return self.__client_bugzoo

    @property
    def finished(self) -> threading.Event:
        """
        An event that is used to indicate when the adaptation process has
        finished.
        """
        return self.__event_finished

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
    def lines(self) -> bugzoo.core.fileline.FileLineSet:
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
    def patches(self) -> List[darjeeling.candidate.Candidate]:
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
                    self.__patches.append(patch)

                # mark the search as finished
                self.__event_finished.set()

            # TODO ensure that thread is killed cleanly
            thread = threading.Thread(target=search)
            thread.start()
