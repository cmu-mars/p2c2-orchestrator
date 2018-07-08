# optimize coverage
# - src/rospack should be caught by unit test
# - exclude any files with less than N suspicious lines
import logging

import darjeeling
from bugzoo.core.coverage import TestSuiteCoverage
from bugzoo.core.fileline import FileLineSet
from bugzoo.util import indent
from darjeeling.localization import Localization

from .exceptions import FailedToComputeCoverage

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# FIXME ADD SANITY CHECKING
def localize(coverage: TestSuiteCoverage) -> Localization:
    def suspiciousness(ep: int, np: int, ef: int, nf: int) -> float:
        # FIXME greedy!
        # return 1.0 if nf == 0 and ep == 0 else 0.0
        if nf != 0:
            return 0.0
        return np / (ep + np + 1)
        # return 1.0 if nf == 0 else 0.0

    logger.info("computing fault localization")
    logger.debug("passing coverage:\n%s", coverage.passing)
    logger.debug("failing coverage:\n%s", coverage.failing)

    try:
        localization = Localization.from_coverage(coverage, suspiciousness)
        logger.debug("ignoring files that contain only one suspicious line")
        lines = FileLineSet.from_iter(localization)
        files = set(localization.files)
        logger.debug("files: %s", files)
        keep_files = set()
        for fn in files:
            lines_in_file = list(lines[fn])
            logger.debug("lines in file (%s): %s", fn, lines_in_file)
            if len(lines_in_file) > 1:
                logger.debug("keeping file: %s", fn)
                keep_files.add(fn)
            else:
                logger.debug("dropping file: %s", fn)
        drop_files = set(files) - set(keep_files)
        logger.debug("ignoring files: %s", drop_files)
        lines = lines.restricted_to_files(keep_files)
        localization = localization.restricted_to_lines(list(lines))

    except darjeeling.exceptions.NoImplicatedLines:
        raise FailedToComputeCoverage
    logger.info("computed fault localization (%d files, %d lines):\n%s",
                len(localization.files), len(localization), localization)
    lines = FileLineSet.from_list([l for l in localization])
    logger.info("suspicious lines:\n%s", indent(repr(lines), 2))
    return localization
