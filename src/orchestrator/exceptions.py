from typing import Optional

import flask
from bugzoo.core.fileline import FileLine


__all__ = [
    'OrchestratorError',
    'PerturbationFailure',
    'NeutralPerturbation',
    'FailedToComputeCoverage',
    'NotReadyToPerturb',
    'NotReadyToAdapt',
    'FileNotFound',
    'LineNotFound',
    'NoSearchLimits',
    'OperatorNotFound'
]


class OrchestratorError(Exception):
    def to_response(self) -> flask.Response:
        raise NotImplementedError

    def _to_response(self,
                     message: str,
                     kind: Optional[str] = None,
                     code: int = 400
                     ) -> flask.Response:
        if kind is None:
            kind = self.__class__.__name__
        jsn = {
            'error': {
                'kind': kind,
                'message': message
            }
        }
        return flask.make_response(flask.jsonify(jsn), code)


class PerturbationFailure(OrchestratorError):
    """
    Indicates that a given perturbation failed to be injected into the baseline
    system.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("failed to build perturbation.")


class NeutralPerturbation(PerturbationFailure):
    """
    Indicates that the perturbation does not introduce any test failures.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("invalid perturbation: no test failures.")


class FailedToComputeCoverage(PerturbationFailure):
    """
    Indicates that coverage information could not be obtained for the perturbed
    system.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("invalid perturbation: failed to obtain coverage information.")


# FIXME replace with AlreadyPerturbed and AlreadyStartedAdaptation
class NotReadyToPerturb(OrchestratorError):
    """
    Indicates that the system is not in a state where a perturbation may be
    legally injected.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("system is not ready to be perturbed.", code=409)


class NotReadyToAdapt(OrchestratorError):
    """
    Indicates that the system is not in a state where a perturbation may be
    legally injected.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("system is not ready to be adapted.", code=409)


class NoSearchLimits(OrchestratorError):
    """
    Indicates that the user attempted to perform adaptation without specifying
    any limits on the available resources.
    """
    def to_response(self) -> flask.Response:
        return self._to_response("no resource limits specified.", code=400)


class FileNotFound(OrchestratorError):
    """
    Indicates that the given file either does not exist or if it does, it
    cannot be subject to perturbation.
    """
    def __init__(self, filename: str) -> None:
        self.__filename = filename
        super().__init__()

    @property
    def filename(self) -> str:
        """
        The name of the file.
        """
        return self.__filename

    def to_response(self) -> flask.Response:
        msg = "file could not be found: {}".format(self.filename)
        return self._to_response(msg)


class LineNotFound(OrchestratorError):
    """
    Indicates that the given line either does not exist or if it does, it
    cannot be subject to perturbation.
    """
    def __init__(self, line: FileLine) -> None:
        self.__line = line
        super().__init__()

    @property
    def line(self) -> FileLine:
        """
        The line.
        """
        return self.__line

    @property
    def filename(self) -> str:
        """
        The name of the file to which the line belongs.
        """
        return self.__line.filename

    @property
    def line_num(self) -> int:
        """
        The one-indexed number of the line within its file.
        """
        return self.__line.num

    def to_response(self) -> flask.Response:
        msg = "line could not be found: {}".format(self.line)
        return self._to_response(msg)


class OperatorNotFound(OrchestratorError):
    """
    Indicates that no mutation operator could be found with a given name.
    """
    def __init__(self, operator: str) -> None:
        self.__operator = operator
        super().__init__()

    @property
    def operator(self) -> str:
        """
        The name of the operator.
        """
        return self.__operator

    def to_response(self) -> flask.Response:
        msg = "mutation operator could not be found: {}".format(self.operator)
        return self._to_response(msg)
