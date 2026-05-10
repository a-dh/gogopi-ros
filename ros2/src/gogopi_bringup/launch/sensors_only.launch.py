from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("frame_id", default_value="sonar_link"),
        DeclareLaunchArgument("camera_device", default_value="/dev/video0"),
        DeclareLaunchArgument("camera_frame_id", default_value="camera_link"),
        DeclareLaunchArgument("thermal_frame_id", default_value="thermal_camera_link"),
        DeclareLaunchArgument("thermal_refresh_hz", default_value="1.0"),
        DeclareLaunchArgument("lidar_port", default_value="/dev/ttyUSB0"),

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
