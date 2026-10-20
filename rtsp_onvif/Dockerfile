FROM python:3.12-slim-trixie

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev
ARG GO2RTC_VERSION=1.9.14
# Scrypted's detection model (MIT), pinned to a scrypted/plugin-models commit
ARG MODEL=https://huggingface.co/scrypted/plugin-models/resolve/187402b20a3be0a7fab9bceee21d857ed6ae0064/openvino/scrypted_yolov9c_relu_int8/best-converted

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata fonts-dejavu-core ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# OpenVINO wheels need glibc, hence Debian rather than Home Assistant's Alpine base
RUN pip install --no-cache-dir openvino==2026.4.1 numpy==2.5.4
RUN case "${BUILD_ARCH}" in aarch64) arch=arm64 ;; amd64) arch=amd64 ;; *) echo "unsupported arch ${BUILD_ARCH}" && exit 1 ;; esac \
    && curl -fsSL -o /usr/local/bin/go2rtc "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${arch}" \
    && chmod +x /usr/local/bin/go2rtc
RUN mkdir /model \
    && curl -fsSL -o /model/model.xml "${MODEL}.xml" \
    && curl -fsSL -o /model/model.bin "${MODEL}.bin"

COPY app /app
WORKDIR /
CMD ["python3", "-u", "-m", "app"]

LABEL io.hass.type="addon" \
      io.hass.name="RTSP to ONVIF" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/krasnoukhov/hassio-rtsp-onvif" \
      org.opencontainers.image.licenses="MIT"
