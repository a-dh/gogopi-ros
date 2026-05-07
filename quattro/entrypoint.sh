#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash

URDF="$(cat /config/gogopi.urdf)"

# robot_state_publisher — publishes TF from URDF + /joint_states
ros2 run robot_state_publisher robot_state_publisher \
  --ros-args -p robot_description:="$URDF" &

# joint_state_publisher_gui — slider GUI, drives /joint_states
ros2 run joint_state_publisher_gui joint_state_publisher_gui &

# rviz2 — foreground, exit closes the container
rviz2 -d /config/default.rviz
