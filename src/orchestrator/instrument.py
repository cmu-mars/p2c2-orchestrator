import bugzoo
import docker

from .orchestrator import fetch_baseline_snapshot


def instrument() -> None:
    client_docker = docker.client.from_env()
    client_docker.images.remove("cmumars/cp2:instrument", force=True)

    with bugzoo.server.ephemeral(verbose=True) as client_bugzoo:
        # destroy any existing image

        snapshot = fetch_baseline_snapshot(client_bugzoo)
        mgr_ctr = client_bugzoo.containers
        container = None
        try:
            container = mgr_ctr.provision(snapshot)
            mgr_ctr.instrument(container)
            mgr_ctr.persist(container, "cmumars/cp2:instrument")
        finally:
            if container:
                del mgr_ctr[container.uid]
