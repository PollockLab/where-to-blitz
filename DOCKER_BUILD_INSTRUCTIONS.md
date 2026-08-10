Files added/modified by this change

- Dockerfile
- .github/workflows/build-push-image.yml
- DOCKER_BUILD_INSTRUCTIONS.md

Branch:
- sota/87-projected-5km-grid (committed locally)

Purpose

This adds a minimal builder Docker image that installs system GDAL and related libraries
(libtiff, libwebp) and Python packages from requirements.txt. The GitHub Actions workflow
build-push-image.yml is a manual workflow that builds and pushes the image to GitHub
Container Registry (ghcr.io) using the repository owner's namespace.

Build instructions (local)

1. Build locally (no push):

   docker build -t where-to-blitz-builder:latest -f Dockerfile .

2. Build and push to GHCR (requires you to be logged in to ghcr.io):

   # Login to GHCR (use a PAT with read:packages/write:packages or use GITHUB_TOKEN in Actions)
   echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

   docker buildx build \
     --platform linux/amd64 \
     -t ghcr.io/<OWNER>/where-to-blitz-builder:latest \
     -t ghcr.io/<OWNER>/where-to-blitz-builder:$(git rev-parse --short HEAD) \
     --push \
     -f Dockerfile .

Replace <OWNER> with your GitHub username or organization.

Build instructions (GitHub Actions)

- Trigger the workflow: .github/workflows/build-push-image.yml is configured for manual
  dispatch (workflow_dispatch). It uses the automatically-provided GITHUB_TOKEN to log
  in and push to ghcr.io/${{ github.repository_owner }}/where-to-blitz-builder.

Testing

- Run tests inside the container (mount the repository):

  docker run --rm -it -v "$(pwd)":/workspace -w /workspace where-to-blitz-builder:latest \
    bash -lc "pytest -q test_ingest_stats.py"

Notes and rationale

- Base image: python:3.11-slim to keep image small while providing Python runtime.
- System packages installed only include those required to build/run GDAL and related
  libraries (gdal-bin, libgdal-dev, libtiff-dev, libwebp-dev). Additional build tools
  (build-essential, git) are included to allow pip build from source when wheels are
  unavailable.
- The Dockerfile creates a virtual environment at /opt/venv and installs Python
  dependencies there; PATH is updated so the environment is active by default.
- The workflow uses docker/build-push-action with GitHub Actions cache (type=gha)
  which helps reuse build layers (including apt and pip layers) between runs.

No secrets are hard-coded. The workflow relies on GITHUB_TOKEN and github.actor for
authentication to ghcr.io.
