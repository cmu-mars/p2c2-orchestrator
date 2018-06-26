from typing import List, Dict, Any
import logging
import json
import sys

import rooibos
import bugzoo.server
from rooibos import Client as RooibosClient
from bugzoo.client import Client as BugZooClient
from bugzoo.core.bug import Bug as Snapshot
from darjeeling.snippet import Snippet
from boggart.server.sourcefile import SourceFileManager
from boggart.config.operators import Operators
from boggart.core import FileLocationRange, Location

from .orchestrator import fetch_baseline_snapshot

logger = logging.getLogger(__name__)  # type: logger.Logging
logger.setLevel(logging.DEBUG)


def extract() -> None:
    dest_fn = sys.argv[1]
    logger.info("writing snippets to %s", dest_fn)
    files = [
        'src/ros_comm/roscpp/src/libros/transport_subscriber_link.cpp'
    ]
    with rooibos.ephemeral_server() as client_rooibos:
        with bugzoo.server.ephemeral() as client_bugzoo:
            snapshot = fetch_baseline_snapshot(client_bugzoo)
            extract_guards(client_rooibos,
                           client_bugzoo,
                           files,
                           snapshot,
                           dest_fn)


def extract_guards(client_rooibos: RooibosClient,
                   client_bugzoo: BugZooClient,
                   files: List[str],
                   snapshot: Snapshot,
                   fn_destination: str
                   ) -> List[Snippet]:
    schema = "if (:[1])"
    snippets = []  # type: List[Dict[str, Any]]

    # fetch the contents of all of the files
    logger.info("storing contents of source files")
    mgr_file = SourceFileManager(client_bugzoo,
                                 client_rooibos,
                                 Operators())
    mgr_file._fetch_files(snapshot, files)
    logger.info("stored contents of source files")

    logger.info("finding snippets")
    for fn in files:
        logger.info("finding snippets in file: %s", fn)
        file_content = mgr_file.read_file(snapshot, fn)
        for match in client_rooibos.matches(file_content, schema):
            snippet_content = match.environment['1'].fragment
            loc_range_rooibos = match.environment['1'].location
            loc_start = Location(loc_range_rooibos.start.line,
                                 loc_range_rooibos.start.col)
            loc_stop = Location(loc_range_rooibos.stop.line,
                                 loc_range_rooibos.stop.col)
            snippet_location = FileLocationRange(fn, loc_start, loc_stop)
            snippet = {
                'content': snippet_content,
                'location': str(snippet_location)
            }
            logger.info("found snippet in file (%s): %s",
                        fn, snippet_content)
            snippets.append(snippet)
        logger.info("found all snippets in file: %s", fn)

    logger.info("found %d snippets", len(snippets))

    logger.info("dumping snippets to file")
    with open(fn_destination, 'w') as f:
        json.dump(snippets, f, indent=2)
    logger.info("dumped snippets to file")

    return snippets
