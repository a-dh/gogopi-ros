FROM osrf/ros:jazzy-desktop

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-jazzy-robot-state-publisher \
      ros-jazzy-joint-state-publisher-gui \
    && rm -rf /var/lib/apt/lists/*

COPY fastdds_tailscale.xml /config/fastdds_tailscale.xml
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV RMW_FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_tailscale.xml
ENV ROS_DOMAIN_ID=0

ENTRYPOINT ["/entrypoint.sh"]
