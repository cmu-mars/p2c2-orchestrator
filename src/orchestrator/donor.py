from typing import List, Dict, Any, Tuple, Callable, Optional
import logging
import json
import sys
import functools
import os

import rooibos
import bugzoo.server
from rooibos import Client as RooibosClient
from bugzoo.client import Client as BugZooClient
from bugzoo.core.bug import Bug as Snapshot
from darjeeling.snippet import Snippet, SnippetDatabase
from darjeeling.source import ProgramSourceManager
from boggart.config.operators import Operators
from boggart.core import FileLocationRange, Location
from boggart.core.constraint import Constraint, IsSingleTerm, PrecededBy

from .snapshot import fetch_baseline_snapshot
from .coverage import load_baseline_coverage

logger = logging.getLogger(__name__)  # type: logger.Logging
logger.setLevel(logging.DEBUG)

# FIXME
DONOR_POOL_FN = os.path.join(os.path.dirname(__file__),
                             'data/transformer.snippets.json')


def extract() -> None:
    with rooibos.ephemeral_server() as client_rooibos:
        coverage = load_baseline_coverage()
        files = list(coverage.lines.files)

        logger.info("storing contents of source files")
        with bugzoo.server.ephemeral() as client_bugzoo:
            snapshot = fetch_baseline_snapshot(client_bugzoo)
            sources = \
                ProgramSourceManager(client_bugzoo,
                                     client_rooibos,
                                     snapshot,
                                     files)
        logger.info("stored contents of source files")

        build_guard_pool('guard.snippets.json',
                         client_rooibos,
                         sources)
        build_transformer_pool('transformer.snippets.json',
                               client_rooibos,
                               sources)
        build_void_call_pool('void-call.snippets.json',
                             client_rooibos,
                             sources)


def load_pool() -> None:
    with open(DONOR_POOL_FN, 'r') as f:
        jsn = json.load(f)
    snippets = SnippetDatabase.from_dict(jsn)
    return snippets


def build_guard_pool(dest_fn: str,
                     client_rooibos: RooibosClient,
                     sources: ProgramSourceManager
                     ) -> None:
    schema = "if (:[1])"
    def transformer(match: rooibos.Match
                    ) -> Tuple[str, rooibos.LocationRange]:
        content = match.environment['1'].fragment.strip()
        location = match.environment['1'].location
        return (content, location)
    _build_pool(dest_fn, client_rooibos, sources, schema, transformer)


def build_transformer_pool(dest_fn: str,
                           client_rooibos: RooibosClient,
                           sources: ProgramSourceManager
                           ) -> None:
    constraints = [
        IsSingleTerm('1'),
        IsSingleTerm('2'),
        IsSingleTerm('3')
    ]
    schema = ":[1] = :[2](:[3]);"
    def transformer(match: rooibos.Match
                    ) -> Tuple[str, rooibos.LocationRange]:
        content = match.environment['2'].fragment.strip()
        location = match.environment['2'].location
        return (content, location)
    _build_pool(dest_fn, client_rooibos, sources, schema, transformer,
                constraints=constraints)


def build_void_call_pool(dest_fn: str,
                         client_rooibos: RooibosClient,
                         sources: ProgramSourceManager
                         ) -> None:
    constraints = [
        IsSingleTerm('1'),
        PrecededBy([';', '{', '}'])
    ]
    schema = ":[1]();"
    def transformer(match: rooibos.Match
                    ) -> Tuple[str, rooibos.LocationRange]:
        content = match.environment['1'].fragment.strip()
        location = match.environment['1'].location
        return (content, location)
    _build_pool(dest_fn, client_rooibos, sources, schema, transformer,
                constraints=constraints)


def _build_pool(dest_fn: str,
                client_rooibos: RooibosClient,
                sources: ProgramSourceManager,
                schema: str,
                transformer: Callable[[rooibos.Match], Tuple[str, rooibos.LocationRange]],
                *,
                constraints: Optional[List[Constraint]] = None
                ) -> None:
    # build a constraint checker
    if constraints is None:
        constraints = []

    def check_constraints(match: rooibos.Match, filename: str) -> bool:
        file_content = sources.read_file(filename)
        get_offset = \
            lambda line, col: sources.line_col_to_offset(filename, line, col)

        loc_start = match.location.start
        loc_stop = match.location.stop

        offset_start = get_offset(loc_start.line, loc_start.col)
        offset_stop = get_offset(loc_start.line, loc_start.col)

        for c in constraints:
            sat = c.is_satisfied_by(match, file_content, offset_start, offset_stop)
            if not sat:
                return False

        return True

    snippets = SnippetDatabase()
    logger.info("finding snippets")
    for fn in sources.files:
        file_content = sources.read_file(fn)
        logger.info("finding snippets in file: %s", fn)
        for match in client_rooibos.matches(file_content, schema):
            if not check_constraints(match, fn):
                continue

            snippet_content, loc_range_rooibos = transformer(match)
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
