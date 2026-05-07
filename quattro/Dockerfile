FROM osrf/ros:jazzy-desktop

COPY fastdds_tailscale.xml /config/fastdds_tailscale.xml

ENV RMW_FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_tailscale.xml
ENV ROS_DOMAIN_ID=0

RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

CMD ["bash", "-c", "source /opt/ros/jazzy/setup.bash && rviz2"]
