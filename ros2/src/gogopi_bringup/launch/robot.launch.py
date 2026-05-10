from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("wheel_separation_m", default_value="0.15"),
        DeclareLaunchArgument("max_speed_pct", default_value="80"),
        DeclareLaunchArgument("frame_id", default_value="sonar_link"),

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
    ])
