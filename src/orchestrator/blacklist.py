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
        'src/ros_comm/xmlrpcpp/src/XmlRpcUtil.cpp'
    ]
    return fn not in blacklist
