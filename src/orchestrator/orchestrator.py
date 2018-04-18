import threading
import time

import hulk
import bugzoo


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
        self.__event_finished = threading.Event()

        self.__state = OrchestratorState.READY_TO_PERTURB
        self.__client_hulk = hulk.Client(url_hulk)
        self.__client_bugzoo = bugzoo.Client(url_bugzoo)
        # TODO it would be nicer if Darjeeling was a service

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
