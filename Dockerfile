# Minimal builder image with system deps for raster work (GDAL + Python bindings)
# Base: python:3.11-slim to keep image small while providing Python

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    VENV_DIR=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Install minimal system packages required to build and run GDAL/Python extensions
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       ca-certificates \
       gdal-bin \
       libgdal-dev \
       libtiff-dev \
       libwebp-dev \
       python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create and activate a virtual environment to keep Python deps isolated
RUN python -m venv $VENV_DIR \
    && pip install --upgrade pip setuptools wheel

# Copy only requirements first so Docker layer can be cached
COPY requirements.txt /tmp/requirements.txt

# Install Python deps (rio-pmtiles is pinned in requirements.txt; no git build needed)
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Working directory for runtime invocations
WORKDIR /workspace

# Allow build-time override of which release provides the small inputs
ARG GRID_INPUTS_RELEASE=grid-inputs-v1

# Attempt to bake small supporting rasters into the image to speed CI. If the release
# asset isn't available or download fails, the build continues (the CI fallback still
# generates/downloads them at runtime).
RUN mkdir -p /workspace/cluster_results/ca \
    && set -x \
    && REL="$GRID_INPUTS_RELEASE" \
    && curl -fsSL "https://github.com/PollockLab/where-to-blitz/releases/download/${REL}/ca_travel_time.tif" -o /workspace/cluster_results/ca/ca_travel_time.tif || echo "ca_travel_time not baked" \
    && curl -fsSL "https://github.com/PollockLab/where-to-blitz/releases/download/${REL}/ca_bioclim.tif" -o /workspace/cluster_results/ca/ca_bioclim.tif || echo "ca_bioclim not baked" \
    && curl -fsSL "https://github.com/PollockLab/where-to-blitz/releases/download/${REL}/ca_forestloss.tif" -o /workspace/cluster_results/ca/ca_forestloss.tif || echo "ca_forestloss not baked"

# Default to an interactive shell. Users can override CMD when running the container.
CMD ["bash"]
