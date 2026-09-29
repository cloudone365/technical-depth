# A minimal "fake node": sshd + python3, arm64-native on the Spark.
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      openssh-server python3 sudo iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd \
 && useradd -m -s /bin/bash nvidia \
 && echo 'nvidia ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/nvidia \
 && sed -i 's/^#\?MaxStartups.*/MaxStartups 200:30:400/' /etc/ssh/sshd_config
COPY authorized_keys /home/nvidia/.ssh/authorized_keys
RUN chown -R nvidia:nvidia /home/nvidia/.ssh && chmod 700 /home/nvidia/.ssh && chmod 600 /home/nvidia/.ssh/authorized_keys
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
