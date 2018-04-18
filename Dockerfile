# Provides a minimal installation of Python 3.6 and pip, along with
# the orchestrator library and mock server.
FROM alpine:3.7

# credit to: https://hub.docker.com/r/frolvlad/alpine-python3
RUN apk add --no-cache python3 \
 && python3 -m ensurepip \
 && rm -r /usr/lib/python*/ensurepip \
 && pip3 install --upgrade pip setuptools \
 && if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi \
 && if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi \
 && rm -r /root/.cache

ADD . /opt/orchestrator
RUN cd /opt/orchestrator \
 && pip install . \
 && rm -r /root/.cache \
 && rm -rf /opt/orchestrator
