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
        'build/'
        'src/geometry2/tf2_ros/test',
        'src/rospack/test',
        'src/ros/roslib/test',
        'src/pcl_conversions/test',
        'src/navigation/voxel_grid/test',
        'src/laser_geometry/test',
        'src/angles/test',
    ]
    return not any(fn.startswith(b) for b in blacklist)
