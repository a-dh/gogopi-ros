#!/bin/bash
# Start all gopiros2 ROS2 pieces in a tmux session.
# Usage: ./start.sh [session-name]
#
# Windows:
#   0 daemons  — ensure pigpiod + rpi-led-proxy are running
#   1 urdf     — robot_state_publisher (publishes /robot_description + TF)
#   2 board    — board_node (lifecycle: configure then activate)
#   3 shell    — interactive shell, pre-sourced

SESSION="${1:-gopiros2}"
WS="/home/albertd/gopiros2/ros2"
REPO="/home/albertd/gopiros2"
SETUP="source /opt/ros/jazzy/setup.bash && source $WS/install/setup.bash && export PYTHONPATH=$REPO:\$PYTHONPATH"

tmux kill-session -t "$SESSION" 2>/dev/null

# 0: daemons
tmux new-session -d -s "$SESSION" -n "daemons" -x 220 -y 50
tmux send-keys -t "$SESSION:daemons" \
  "sudo systemctl start pigpiod rpi-led-proxy && systemctl status pigpiod rpi-led-proxy --no-pager" Enter

# 1: robot_state_publisher
tmux new-window -t "$SESSION" -n "urdf"
tmux send-keys -t "$SESSION:urdf" \
  "$SETUP && ros2 launch /home/albertd/gopiros2/test_urdf.launch.py" Enter

# 2: board_node — start node, wait for it to register, then configure + activate
tmux new-window -t "$SESSION" -n "board"
tmux send-keys -t "$SESSION:board" \
  "$SETUP && ros2 run gogopi_bringup board_node" Enter

# 3: shell — configure+activate after board_node is ready, then stay interactive
tmux new-window -t "$SESSION" -n "shell"
tmux send-keys -t "$SESSION:shell" \
  "$SETUP && sleep 4 \
  && ros2 service call /board_node/change_state lifecycle_msgs/srv/ChangeState '{transition: {id: 1}}' \
  && sleep 1 \
  && ros2 service call /board_node/change_state lifecycle_msgs/srv/ChangeState '{transition: {id: 3}}'" Enter

tmux select-window -t "$SESSION:board"

cat <<'EOF'
┌─ gopiros2 tmux session ──────────────────────────────────┐
│  Ctrl-b 0  daemons  — pigpiod + rpi-led-proxy status     │
│  Ctrl-b 1  urdf     — robot_state_publisher              │
│  Ctrl-b 2  board    — board_node (open here on attach)   │
│  Ctrl-b 3  shell    — interactive shell, auto-lifecycle  │
│                                                          │
│  Ctrl-b d  detach   (session keeps running in bg)        │
│  Ctrl-b [  scroll mode  (q to exit)                      │
│  ./start.sh         restart everything                   │
└──────────────────────────────────────────────────────────┘
EOF

tmux attach-session -t "$SESSION"
