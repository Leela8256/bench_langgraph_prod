# Smoke client — RocketRide SDK only, pinned to the version the PDF
# benchmark verified against.
FROM --platform=linux/amd64 python:3.12-slim-bookworm
RUN pip install --no-cache-dir "rocketride==1.3.0"
WORKDIR /app
COPY smoke_video.py .
