FROM alpine:3.7
WORKDIR /opt/orchestrator
COPY setup.py
COPY src
COPY tests
