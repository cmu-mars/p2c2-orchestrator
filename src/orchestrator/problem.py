__all__ = ['Problem']

from typing import Callable, Optional

import darjeeling.problem
from rooibos import Client as RooibosClient
from boggart.core.mutant import Mutant
from bugzoo.client import Client as BugZooClient
from bugzoo.core.patch import Patch
from bugzoo.core.container import Container
from bugzoo.core.coverage import TestSuiteCoverage
from bugzoo.cmd import ExecResponse
from bugzoo.compiler import CompilationOutcome as BuildOutcome
from kaskara import Analysis

STACKS = [
    'ros_comm',
    'kobuki',
    'kobuki_core',
    'turtlebot',
    'turtlebot_apps',
    'turtlebot_create',
    'turtlebot_simulator',
    'bond_core',
    'ecl_core',
    'geometry',
    'geometry2',
    'common_msgs',
    'diagnostics',
    'ecl_lite',
    'ecl_navigation',
    'ecl_tools',
    'freenect_stack',
    'image_common',
    'image_pipeline',
    'navigation',
    'nodelet_core',
    'openni2_camera',
    'orocos_kinematics_dynamics',
    'perception_pcl',
    'rocon_app_platform',
    'rocon_msgs',
    'rocon_multimaster',
    'rocon_tools',
    'ros',
    'ros_comm_msgs',
    'ros_tutorials',
    'roscpp_core',
    'slam_gmapping',
    'unique_identifier',
    'urdf',
    'vision_opencv',
    'yujin_ocs',
    'zeroconf_avahi_suite'
]


class Problem(darjeeling.problem.Problem):
    def __init__(self,
                 client_bugzoo: BugZooClient,
                 client_rooibos: RooibosClient,
                 coverage: TestSuiteCoverage,
                 mutant: Mutant,
                 analysis: Analysis
                 ) -> None:
        self.__client_bugzoo = client_bugzoo
        snapshot = client_bugzoo.bugs[mutant.snapshot]
        super().__init__(bz=client_bugzoo,
                         bug=snapshot,
                         coverage=coverage,
                         client_rooibos=client_rooibos,
                         analysis=analysis)

    def build_patch(self,
                    patch: Patch,
                    builder: Optional[Callable[[Container], BuildOutcome]] = None
                    ) -> Container:
        mgr_ctr = self.__client_bugzoo.containers

        # determine the modified package
        fn = patch.files[0]
        # fn = fn[4:]  # strip "src/"
        path = fn.split('/')[1:]

        if path[0] in STACKS:
            pkg = path[1]
        else:
            pkg = path[0]

        if builder is None:
            cmd = 'catkin build {} --no-deps --no-status -j1 --override-build-tool-check'.format(pkg)
            builder = lambda c: BuildOutcome(mgr_ctr.exec(c, cmd, '/ros_ws'))
        return super().build_patch(patch, builder)
