FROM alpine:3.7

# credit to: https://hub.docker.com/r/frolvlad/alpine-python3
RUN apk add --no-cache python3 && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --upgrade pip setuptools && \
    if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
    if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi && \
    rm -r /root/.cache

WORKDIR /opt/orchestrator
ADD . /opt/orchestrator
RUN pip install . && \
    rm -r /root/.cache
ENTRYPOINT ["orchestrator"]
