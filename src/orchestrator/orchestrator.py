from typing import List
import threading
import time

import hulk
import bugzoo
import darjeeling


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

        self.__problem = None # type: Optional[darjeeling.problem.Problem]

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
        files = [fn for fn in lines.files if __is_file_mutable(fn)]
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
        raise NotImplementedError

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
                    self.__problem = darjeeling.Problem(bz=self.bugzoo,
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
