from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("frame_id", default_value="sonar_link"),

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
    ])
