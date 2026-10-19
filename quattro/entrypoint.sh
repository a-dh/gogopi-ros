#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash

# No robot_state_publisher here: the robot's (gprctl urdf window) publishes
# /robot_description and TF from the real servo positions (/gogopi/servo_state).

# joint_state_publisher_gui — slider GUI, drives /joint_states → board_node moves
# the pan/tilt servos. Reads the URDF from the robot's /robot_description topic.
ros2 run joint_state_publisher_gui joint_state_publisher_gui &

# rviz2 — foreground, exit closes the container
rviz2 -d /config/default.rviz
