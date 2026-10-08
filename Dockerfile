# Single-stage: colcon build for an ament_python package is cheap (no C++
# compilation), so a build/runtime split buys little here.
FROM ros:jazzy-ros-base

WORKDIR /workspace
COPY perception/src/detector src/detector

# cv_bridge's apt package pulls in a full desktop OpenCV/GStreamer/Qt/GTK
# stack via Recommends, none of it needed in a headless container.
RUN echo 'APT::Install-Recommends "0";' > /etc/apt/apt.conf.d/99no-recommends

# rosdep resolves package.xml's own deps; foxglove_bridge and
# compressed_image_transport are the separate tools the launch file and
# remote viewing rely on, so they're installed explicitly.
RUN apt-get update && \
    rosdep update && \
    rosdep install --from-paths src --ignore-src -r -y && \
    apt-get install -y --no-install-recommends \
      python3-pip \
      ros-jazzy-foxglove-bridge \
      ros-jazzy-compressed-image-transport && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# onnxruntime has no apt/rosdep package for its Python bindings on this
# base image, so it's pip-only. ros:jazzy-ros-base has no pip preinstalled,
# hence python3-pip above.
RUN pip3 install --break-system-packages --no-cache-dir onnxruntime

RUN /bin/bash -c "source /opt/ros/jazzy/setup.bash && colcon build"

# There's no .git in this image, so the running code version is baked in
# from CI instead, sourced from the release tag. Placed late so it doesn't
# invalidate earlier layers.
ARG VERSION=unknown
ENV CODE_VERSION=$VERSION
LABEL org.opencontainers.image.version=$VERSION

# Device-side health signal, invoked via `docker exec`, not part of CMD.
COPY agent/healthcheck.py /workspace/healthcheck.py

COPY perception/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "detector", "pipeline.launch.py", "source:=0", "compressed:=true"]
