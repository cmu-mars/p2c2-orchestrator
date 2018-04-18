from typing import Optional

import flask


__ALL__ = [
    'OrchestratorError',
    'PerturbationFailure',
    'NeutralPerturbation',
    'FailedToComputeCoverage',
    'NotReadyToPerturb',
    'NotReadyToAdapt',
    'FileNotFound'
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
