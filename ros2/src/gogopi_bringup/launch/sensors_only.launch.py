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
        DeclareLaunchArgument("frame_id", default_value="sonar_link"),
        DeclareLaunchArgument("camera_device", default_value="/dev/video0"),
        DeclareLaunchArgument("camera_frame_id", default_value="camera_link"),
        DeclareLaunchArgument("thermal_frame_id", default_value="thermal_camera_link"),
        DeclareLaunchArgument("thermal_refresh_hz", default_value="1.0"),
        DeclareLaunchArgument("lidar_port", default_value="/dev/ydlidar"),

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
            executable="sonar_node",
            name="sonar_node",
            namespace="",
            output="screen",
            parameters=[{
                "frame_id": LaunchConfiguration("frame_id"),
            }],
        ),

        LifecycleNode(
            package="gogopi_bringup",
            executable="camera_node",
            name="camera_node",
            namespace="",
            output="screen",
            parameters=[{
                "camera_device": LaunchConfiguration("camera_device"),
                "frame_id": LaunchConfiguration("camera_frame_id"),
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
                "frame_id": LaunchConfiguration("thermal_frame_id"),
                "refresh_hz": LaunchConfiguration("thermal_refresh_hz"),
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
                "port": LaunchConfiguration("lidar_port"),
                "frame_id": "lidar_link",
                "scan_hz": 7.0,
                "single_channel": True,
                "motor_dtr_ctrl": True,
            }],
        ),
    ])
