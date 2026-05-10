#!/usr/bin/env bash
# Install system-level prerequisites for the ROS2 workspace.
# Run once on the robot: sudo ./ros2/setup_system.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

# uhubctl: USB port power control used by lidar_node to cycle the YDLidar USB port.
apt-get install -y uhubctl

# Sudoers rule: members of dialout group may run uhubctl without a password.
# lidar_node and hw_check run as the logged-in user (already in dialout for serial
# access) and invoke 'sudo uhubctl' to power-cycle the YDLidar USB port.
SUDOERS=/etc/sudoers.d/gogopi-ros2
printf '%%dialout ALL=(ALL) NOPASSWD: /usr/sbin/uhubctl\n' > /tmp/gogopi-ros2.sudoers
visudo -c -f /tmp/gogopi-ros2.sudoers
install -o root -g root -m 440 /tmp/gogopi-ros2.sudoers "$SUDOERS"
rm /tmp/gogopi-ros2.sudoers
echo "Installed: $SUDOERS"
