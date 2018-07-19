def is_file_mutable(fn: str) -> bool:
    """
    Determines whether a given source code file may be the subject of
    perturbation on the basis of its name.
    """
    if not fn.endswith('.cpp'):
        return False

    #
    # TODO: ignore blacklisted files (e.g., Gazebo, ROS core code)
    #
    blacklist = [
        'src/yujin_ocs/yocs_cmd_vel_mux/src/cmd_vel_subscribers.cpp',
        'src/rospack/src/rospack.cpp',
        'src/ros_comm/xmlrpcpp/src/XmlRpcUtil.cpp',
        'src/actionlib/test',
        'src/geometry/tf/test',
        'src/geometry2/tf2/test',
        'src/kdl_parser/kdl_parser/test',
        'src/roscpp_core/rostime/test',
        'src/class_loader/test',
        'src/navigation/costmap_2d/test',
        'src/navigation/base_local_planner/test',
        'src/ros_comm/message_filters/test',
        'src/pluginlib/test',
        'src/navigation/costmap_2d/test',
        'src/navigation/robot_pose_ekf/test',
        'build/',
        'src/dynamic_reconfigure/test',
        'src/image_common/camera_info_manager/tests',
        'src/diagnostics/diagnostic_updater',
        'src/diagnostics/diagnostic_aggregator',
        'src/navigation/clear_costmap_recovery/test',
        'src/geometry2/tf2_ros/test',
        'src/rospack/test',
        'src/ros/roslib/test',
        'src/pcl_conversions/test',
        'src/navigation/voxel_grid/test',
        'src/laser_geometry/test',
        'src/angles/test',
        'src/geometry2/tf2_kdl/test',
        'src/geometry2/tf2_ros/test',
        'src/navigation/navfn/test',
        'src/geometry2/tf2_py',
        'src/navigation/map_server/test/rtest.cpp',
        'src/bfl/test',
        'src/orocos_kinematics_dynamics/orocos_kdl/tests',

        # no perturbations
        'src/bond_core/bondcpp',
        'src/dynamic_reconfigure/src/dynamic_reconfigure_config_init_mutex.cpp',
        'src/kdl_parser',
        'src/kobuki/kobuki_safety_controller/src/nodelet.cpp',
        'src/ros_comm/message_filters/src/connection.cpp',
        'src/image_common/camera_calibration_parsers/src/parse.cpp',
        'src/geometry/eigen_conversions/src/eigen_msg.cpp',
        'src/vision_opencv',
        'src/image_common/image_transport/src/camera_common.cpp',
        'src/navigation/rotate_recovery/src/rotate_recovery.cpp',
        'src/ros_comm/topic_tools',
        'src/perception_pcl',

        'src/image_pipeline/depth_image_proc/src/nodelets/convert_metric.cpp',
        'src/image_pipeline/depth_image_proc/src/nodelets/crop_foremost.cpp',
        'src/image_pipeline/depth_image_proc/src/nodelets/point_cloud_xyzrgb.cpp',
        'src/image_pipeline/image_proc',

        'src/orocos_kinematics_dynamics/orocos_kdl/src/chain.cpp',

        'src/nodelet_core/nodelet/src/nodelet_class.cpp',
        'src/navigation/robot_pose_ekf/src/odom_estimation_node.cpp',

        # out of scope
        'src/ecl_core/ecl_threads',
        'src/bfl/examples',
        'src/bfl/src/wrappers/rng',
        'src/ros_comm/rosout',
        'src/ros_comm/rosbag',
        'src/stage_ros',
        'src/ros_comm/rosconsole',
        'src/rosconsole_bridge',
        'src/nodelet_core',
        'src/roscpp_core/cpp_common',

        # TEMPORARY
        'src/ros_comm/xmlrpcpp',
        'src/ros_comm/roscpp/src/libros',

        # misbehaving in baseline A
        'src/robot_state_publisher',
        'src/navigation/navfn/src/read_pgm_costmap.cpp',

        # coverage problems
        'src/navigation/voxel_grid'
    ]
    return not any(fn.startswith(b) for b in blacklist)
