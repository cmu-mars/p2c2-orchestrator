from typing import List, Dict, Any, Tuple, Callable
import logging
import json
import sys

import rooibos
import bugzoo.server
from rooibos import Client as RooibosClient
from bugzoo.client import Client as BugZooClient
from bugzoo.core.bug import Bug as Snapshot
from darjeeling.snippet import Snippet, SnippetDatabase
from boggart.server.sourcefile import SourceFileManager
from boggart.config.operators import Operators
from boggart.core import FileLocationRange, Location

from .snapshot import fetch_baseline_snapshot
from .coverage import load_baseline_coverage

logger = logging.getLogger(__name__)  # type: logger.Logging
logger.setLevel(logging.DEBUG)


def extract() -> None:
    with rooibos.ephemeral_server() as client_rooibos:
        coverage = load_baseline_coverage()
        files = list(coverage.lines.files)

        logger.info("storing contents of source files")
        filename_to_contents = {}  # type: Dict[str, str]
        with bugzoo.server.ephemeral() as client_bugzoo:
            snapshot = fetch_baseline_snapshot(client_bugzoo)
            sources = \
                SourceFileManager(client_bugzoo, client_rooibos, Operators())
            sources._fetch_files(snapshot, files)
            for fn in files:
                filename_to_contents[fn] = sources.read_file(snapshot, fn)
        logger.info("stored contents of source files")

        build_guard_pool('guard.snippets.json', client_rooibos, filename_to_contents)


def build_guard_pool(dest_fn: str,
                     client_rooibos: RooibosClient,
                     filename_to_contents: Dict[str, str]
                     ) -> None:
    schema = "if (:[1])"
    def transformer(match: rooibos.Match
                    ) -> Tuple[str, rooibos.LocationRange]:
        content = match.environment['1'].fragment
        location = match.environment['1'].location
        return (content, location)
    _build_pool(dest_fn, client_rooibos, filename_to_contents,
                schema, transformer)


def _build_pool(dest_fn: str,
                client_rooibos: RooibosClient,
                filename_to_contents: Dict[str, str],
                schema: str,
                transformer: Callable[[rooibos.Match], Tuple[str, rooibos.LocationRange]],
                ) -> None:
    snippets = SnippetDatabase()
    logger.info("finding snippets")
    for (fn, file_content) in filename_to_contents.items():
        logger.info("finding snippets in file: %s", fn)
        for match in client_rooibos.matches(file_content, schema):
            snippet_content = match.environment['1'].fragment
            loc_range_rooibos = match.environment['1'].location
            loc_start = Location(loc_range_rooibos.start.line,
                                 loc_range_rooibos.start.col)
            loc_stop = Location(loc_range_rooibos.stop.line,
                                 loc_range_rooibos.stop.col)
            snippet_location = FileLocationRange(fn, loc_start, loc_stop)
            snippets.add(snippet_content,
                         origin=snippet_location)
            logger.info("found snippet in file (%s): %s",
                        fn, snippet_content)
        logger.info("found all snippets in file: %s", fn)

    logger.info("found %d snippets", len(snippets))

    logger.info("dumping snippets to file")
    with open(dest_fn, 'w') as f:
        json.dump(snippets.to_dict(), f, indent=2)
    logger.info("dumped snippets to file")
