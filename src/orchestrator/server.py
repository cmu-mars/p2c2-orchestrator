"""
Implements a RESTlike server that may be used to orchestrate the evaluation of
Phase II CP2. The server connects together the various subsystems involved in
CP2, including the mutant generator (Hulk), distributed test execution platform
(BugZoo), source code transformer (Rooibos), and program repair module
(Darjeeling).

TODO: add locks on all POST methods.
TODO: add top-level logging.
TODO: ensure BugZoo servers are killed cleanly.
"""
from typing import Optional
from enum import Enum
import argparse
import time
import threading

import flask
import hulk
import bugzoo
import rooibos
import darjeeling

from .exceptions import *


app = flask.Flask(__name__) # type: flask.Flask
client_hulk = None # type: Optional[hulk.Client]
client_bugzoo = None # type: Optional[bugzoo.client.Client]
client_rooibos = None # type: Optional[rooibos.Client]

# TODO it might be better to run Darjeeling as its own service
PROBLEM = None # type: Optional[darjeeling.Problem]
SEARCHER = None # type: Optional[darjeeling.Searcher]
PATCHES = [] # type: List[darjeeling.Candidate]


# a list of the names of the mutation operators that may be used to generate
# perturbations
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
    READY_TO_PERTURB = auto()
    PERTURBING = auto()
    READY_TO_ADAPT = auto()
    SEARCHING = auto()
    FINISHED = auto()

STATE = OrchestratorState.READY_TO_PERTURB # type: OrchestratorState
LOCK = threading.Lock() # type: threading.Lock


def __search(minutes: int, num_candidates: int) -> None:
    global SEARCHER
    time_limit = datetime.timedelta(minutes=minutes)
    candidates = \
        darjeeling.generator.SampleByLocalization(problem=PROBLEM,
                                                  localization=PROBLEM.localization,
                                                  snippets=PROBLEM.snippets)
    SEARCHER = darjeeling.searcher.Searcher(bugzoo=client_bugzoo,
                                            problem=PROBLEM,
                                            candidate=candidates,
                                            num_candidates=num_candidates,
                                            time_limit=time_limit)
    for patch in SEARCHER:
        PATCHES.append(patch)


def __get_baseline_snapshot() -> bugzoo.core.bug.Bug:
    """
    Retrieves the BugZoo snapshot for baseline A (i.e., the unperturbed
    system).
    """
    return client_bugzoo.bugs["mars:baseline"]


def __is_file_mutable(fn: str) -> bool:
    """
    Determines whether a source code file may be subject to perturbation.
    """
    if not fn.endswith('.cpp'):
        return False

    #
    # TODO: ignore blacklisted files (e.g., Gazebo, ROS core code)
    #

    return True


def __get_covered_lines() -> bugzoo.core.fileline.FileLineSet:
    """
    Returns the set of all lines in the baseline system that were covered by
    the test suite.
    """
    baseline = __get_baseline_snapshot()
    coverage = client_bugzoo.bugs.coverage(baseline)
    lines = coverage.lines

    # restrict to files that may be mutated
    files = [fn for fn in lines.files if __is_file_mutable(fn)]
    lines = lines.restricted_to_files(files)

    return lines


@app.route('/perturbations', methods=['GET'])
def perturbations():
    """
    Computes a list of all possible perturbations to the source code of the
    system under repair. Optionally, a set of criteria may be provided to
    restrict the set of perturbations.
    """
    fn = flask.request.args.get('file', None)
    line_num = flask.request.args.get('line', None)
    op_name = flask.request.args.get('operator', None)

    if fn is None:
        return 'File must be specified.', 400
    if fn not in __get_covered_lines().files:
        return 'Specified file does not exist or may not be subject to perturbation', 400
    if op_name is not None and op_name is not in OPERATOR_NAMES:
        return 'Operator does not exist.', 400

    # fetch the mutation operators
    if op_name is not None:
        operators = [client_hulk.operators[op_name]]
    else:
        operators = [client_hulk.operators[name] for name in OPERATOR_NAMES]

    mutations = \
        hulk_client.mutations(__get_baseline_snapshot(),
                              filename=fn,
                              operators=operators,
                              line_num=line_num)

    jsn = [m.to_dict() for m in mutations]
    return flask.jsonify(jsn)


@app.route('/lines', methods=['GET'])
def lines():
    """
    Computes a list of all of the lines in the baseline system that may be
    perturbed. Only lines that are covered by the test suite may be perturbed.
    Furthermore, lines in certain blacklisted files are removed from
    consideration, even if covered by the test suite.
    """
    #
    # TODO allow result to be restricted to a given file (pass in URL-encoded args)
    #
    jsn = [str(line) for line in __get_covered_lines()]
    return flask.jsonify(jsn)


@app.route('/files', methods=['GET'])
def files():
    """
    Computes a list of all of the source code files within the baseline system
    that may be subject to perturbation.
    """
    jsn = __get_covered_lines().files
    return flask.jsonify(jsn)


@app.route('/observe', methods=['GET'])
def observe():
    """
    Responds with a summary of the current state of the execution.
    """
    if SEARCHER is None:
        pareto_set = []
        num_attempts = 0
        time_spent = 0.0
    else:
        # FIXME this isn't quite the pareto set
        pareto_set = PATCHES.copy()
        num_attempts = SEARCHER.num_candidate_evals
        time_spent = SEARCHER.time_running


    jsn = {
        'stage': STATE.name,
        'resource-consumption': {
            'num-attempts': num_attempts,
            'time-spent': time_spent
        },
        'pareto-set': pareto_set
    }
    return flask.jsonify(jsn)


@app.route('/adapt', methods=['POST'])
def adapt():
    global STATE, LOCK
    with LOCK:
        if STATE != OrchestratorState.READY_TO_ADAPT:
            return '', 409

        # TODO input validation
        minutes = flask.request.args.get('minutes')
        attempts = flask.request.args.get('attempts')

        if minutes < 1:
            return '', 400
        if attempts < 1:
            return '', 400

        STATE = OrchestratorState.SEARCHING
        threading.Thread(target=__search,
                         args=(minutes, attempts),
                         daemon=True)
        return '', 202


@app.route('/perturb', methods=['POST'])
def perturb():
    """
    Attempts to apply a given perturbation to the baseline system.
    """
    global PROBLEM, STATE

    # fetch the desired perturbation from the payload
    # TODO we could do some input validation here
    perturbation = flask.request.json
    mutant = hulk.base.Mutation.from_dict(perturbation)

    # TODO use lock
    if STATE != OrchestratorState.READY_TO_PERTURB:
        return '', 409

    try:
        STATE = OrchestratorState.PERTURBING

        # TODO catch any unexpected errors
        snapshot = client_hulk.mutate(__get_baseline_snapshot(), mutant)

        # attempt to transform mutant into a repair problem
        # TODO pass logger
        try:
            PROBLEM = darjeeling.Problem(bz=client_bugzoo,
                                         bug=snapshot,
                                         cache_coverage=False)
            STATE = OrchestratorState.READY_TO_ADAPT
            return '', 204
        except darjeeling.exceptions.NoFailingTests:
            raise NeutralPerturbation()
        except darjeeling.exceptions.NoImplicatedLines:
            raise FailedToComputeCoverage()

    except OrchestratorError as err:
        STATE = OrchestratorState.READY_TO_PERTURB
        return err.to_response()


def run(*,
        url_hulk: str,
        url_rooibos: str,
        url_bugzoo: str,
        port: int = 8000
        ) -> None:
    """
    Launches an orchestrator server, and blocks until the server is
    terminated.

    Parameters:
        url_hulk: the base URL of the Hulk server.
        url_rooibos: the base URL of the Rooibos server.
        url_bugzoo: the base URL of the BugZoo server.
        port: the port that the orchestrator should run on.
    """
    global client_hulk
    global client_bugzoo
    global client_rooibos
    assert 0 <= port <= 49151, "invalid port number"

    # TODO: wait for clients
    client_hulk = hulk.Client(url_hulk)
    client_bugzoo = bugzoo.client.Client(url_bugzoo)
    client_rooibos = rooibos.Client(url_rooibos)

    # FIXME
    time.sleep(30)

    # compute and cache coverage
    snapshot = __get_baseline_snapshot()
    coverage = client_bugzoo.bugs.coverage(snapshot.name)

    app.run(port=port)


def main() -> None:
    desc = 'MARS Phase II CP2 Orchestrator -- Welcome to Mars.'
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--hulk',
                        type=str,
                        required=True,
                        help='the base URL of the Hulk server')
    parser.add_argument('--bugzoo',
                        type=str,
                        required=True,
                        help='the base URL of the BugZoo server')
    parser.add_argument('--rooibos',
                        type=str,
                        required=True,
                        help='the base URL of the Rooibos server')
    parser.add_argument('--port',
                        type=int,
                        required=True,
                        help='the port that should be used by this server.')

    args = parser.parse_args()
    run(url_hulk=args.hulk,
        url_rooibos=args.rooibos,
        url_bugzoo=args.bugzoo,
        port=args.port)
