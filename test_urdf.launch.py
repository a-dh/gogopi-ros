from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    urdf_file = os.path.join(get_package_share_directory("gogopi_bringup"), "urdf", "gogopi.urdf")
    with open(urdf_file, "r") as f:
        robot_description = f.read()
    
    return LaunchDescription([
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": robot_description}],
            # TF follows the real servo positions; /joint_states is the GUI's
            # command topic (board_node → servos), same as robot.launch.py
            remappings=[("/joint_states", "/gogopi/servo_state")],
        )
    ])
