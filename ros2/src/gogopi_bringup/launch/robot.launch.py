import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode, Node


def generate_launch_description():
    urdf = os.path.join(
        get_package_share_directory("gogopi_bringup"), "urdf", "gogopi.urdf"
    )
    with open(urdf, "r") as f:
        robot_description = f.read()

    return LaunchDescription([
        DeclareLaunchArgument("wheel_separation_m", default_value="0.15"),
        DeclareLaunchArgument("max_speed_pct", default_value="80"),

        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen",
            parameters=[{"robot_description": robot_description}],
            remappings=[("/joint_states", "/gogopi/servo_state")],
        ),

        LifecycleNode(
            package="gogopi_bringup",
            executable="board_node",
            name="board_node",
            namespace="",
            output="screen",
            parameters=[{
                "wheel_separation_m": LaunchConfiguration("wheel_separation_m"),
                "max_speed_pct": LaunchConfiguration("max_speed_pct"),
            }],
        ),

        LifecycleNode(
            package="gogopi_bringup",
            executable="camera_node",
            name="camera_node",
            namespace="",
            output="screen",
            parameters=[{
                "camera_device": "/dev/video0",
                "frame_id": "camera_link",
                "image_rotation": 0,
            }],
        ),

        LifecycleNode(
            package="gogopi_bringup",
            executable="thermal_node",
            name="thermal_node",
            namespace="",
            output="screen",
            parameters=[{
                "frame_id": "thermal_camera_link",
                "refresh_hz": 1.0,
                "refresh_rate": "REFRESH_2_HZ",
                "image_rotation": 0,
            }],
        ),

        LifecycleNode(
            package="gogopi_bringup",
            executable="lidar_node",
            name="lidar_node",
            namespace="",
            output="screen",
            parameters=[{
                "port": "/dev/ydlidar",
                "baudrate": 115200,
                "frame_id": "lidar_link",
                "scan_hz": 7.0,
                "single_channel": True,
                "motor_dtr_ctrl": True,
            }],
        ),
    ])
