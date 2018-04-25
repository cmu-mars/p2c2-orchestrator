# Provides a minimal installation of Python 3.6 and pip, along with
# the orchestrator library and mock server.
FROM alpine:3.7

# credit to: https://hub.docker.com/r/frolvlad/alpine-python3
RUN apk add --no-cache python3 git gcc gfortran python3-dev build-base openblas-dev \
 && python3 -m ensurepip \
 && rm -r /usr/lib/python*/ensurepip \
 && pip3 install --no-cache --upgrade pip==9.0.3 setuptools \
 && if [[ ! -e /usr/bin/pip ]]; then ln -s pip3 /usr/bin/pip ; fi \
 && if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi

# install Hulk from source
ENV HULK_REVISION 9255e26eaf7d1c2b29b2765637e508c41c6e59cf
RUN git clone https://github.com/squaresLab/Hulk /opt/hulk \
 && cd /opt/hulk \
 && git checkout "${HULK_REVISION}" \
 && pip install --no-cache . \
 && rm -rf /opt/hulk

COPY setup.py /opt/orchestrator/
COPY src/ /opt/orchestrator/src
RUN cd /opt/orchestrator \
 && pip install --no-cache --upgrade . \
 && rm -rf /opt/orchestrator
