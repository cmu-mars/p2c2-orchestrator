"""
Implements a RESTlike server that may be used to orchestrate the evaluation of
Phase II CP2. The server connects together the various subsystems involved in
CP2, including the mutant generator (Hulk), distributed test execution platform
(BugZoo), source code transformer (Rooibos), and program repair module
(Darjeeling).

TODO: add top-level logging.
TODO: ensure BugZoo servers are killed cleanly.
"""
from typing import Optional
import argparse

import flask

from .orchestrator import Orchestrator
from .exceptions import *


app = flask.Flask(__name__) # type: flask.Flask
ORCHESTRATOR = None # type: Optional[Orchestrator]


@app.route('/perturbations', methods=['GET'])
def perturbations():
    fn = flask.request.args.get('file', None)
    line_num = flask.request.args.get('line', None)
    op_name = flask.request.args.get('operator', None)

    mutations = ORCHESTRATOR.perturbations(filename=fn,
                                           line_num=line_num,
                                           op_name=op_name)
    jsn = [m.to_dict() for m in mutations]
    return flask.jsonify(jsn)


@app.route('/lines', methods=['GET'])
def lines():
    #
    # TODO allow result to be restricted to a given file (pass in URL-encoded args)
    #
    lines = ORCHESTRATOR.lines
    jsn = [str(line) for line in lines]
    return flask.jsonify(jsn)


@app.route('/files', methods=['GET'])
def files():
    return flask.jsonify(ORCHESTRATOR.files)


@app.route('/observe', methods=['GET'])
def observe():
    """
    Responds with a summary of the current state of the execution.
    """
    num_attempts, time_spent = ORCHESTRATOR.resource_usage
    # FIXME serialise
    jsn_patches = ORCHESTRATOR.patches
    jsn = {
        'stage': ORCHESTRATOR.state.name,
        'resource-consumption': {
            'num-attempts': num_attempts,
            'time-spent': time_spent
        },
        'pareto-set': jsn_patches
    }
    return flask.jsonify(jsn)


@app.route('/adapt', methods=['POST'])
def adapt():
    minutes = flask.request.args.get('minutes', None)
    attempts = flask.request.args.get('attempts', None)
    try:
        ORCHESTRATOR.adapt(minutes, attempts)
        return '', 202
    except OrchestratorError as err:
        return err.to_response()


@app.route('/perturb', methods=['POST'])
def perturb():
    mutant = hulk.base.Mutation.from_dict(flask.request.json)
    try:
        ORCHESTRATOR.perturb(mutant)
        return '', 204
    except OrchestratorError as err:
        return err.to_response()


def run(*,
        url_hulk: str,
        url_bugzoo: str,
        port: int = 8000
        ) -> None:
    """
    Launches an orchestrator server, and blocks until the server is
    terminated.

    Parameters:
        url_hulk: the base URL of the Hulk server.
        url_bugzoo: the base URL of the BugZoo server.
        port: the port that the orchestrator should run on.
    """
    global ORCHESTRATOR
    assert 0 <= port <= 49151, "invalid port number"
    ORCHESTRATOR = Orchestrator(url_hulk=url_hulk,
                                url_bugzoo=url_bugzoo)
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
    parser.add_argument('--port',
                        type=int,
                        required=True,
                        help='the port that should be used by this server.')

    args = parser.parse_args()
    run(url_hulk=args.hulk,
        url_bugzoo=args.bugzoo,
        port=args.port)
