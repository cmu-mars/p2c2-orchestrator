from typing import Dict, Any
import os

import yaml
from bugzoo.core.bug import Bug as Snapshot
from bugzoo.client import Client as BugZooClient


def _load_manifest() -> Dict[str, Any]:
    fn = os.path.join(os.path.dirname(__file__), 'baseline.yml')
    with open(fn, 'r') as f:
        desc = yaml.load(f)

    # add oracle information
    tests = desc['test-harness']['tests']
    for test in tests:
        has_oracle = 'oracle' in test
        has_kind = 'kind' in test
        if not has_kind and not has_oracle:
            test['oracle'] = {'contains': "[  PASSED  ]"}

        # FIXME only apply during coverage?
        if 'kill-after' not in test:
            test['kill-after'] = 10

    desc['source'] = None
    return desc


def fetch_baseline_snapshot(bz: BugZooClient) -> Snapshot:
    desc = _load_manifest()
    snapshot = Snapshot.from_dict(desc)
    bz.bugs.register(snapshot)
    return snapshot


def fetch_instrumentation_snapshot(bz: BugZooClient) -> Snapshot:
    desc = _load_manifest()
    desc['name'] = 'mars:instrument'
    desc['image'] = 'cmumars/cp2:instrument'
    snapshot = Snapshot.from_dict(desc)
    bz.bugs.register(snapshot)
    return snapshot
