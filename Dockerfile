FROM ubuntu:24.04
ENV PATH="/root/miniforge3/bin:${PATH}"
ARG PATH="/root/miniforge3/bin:${PATH}"

LABEL maintainer="lars.buntemeyer@hereon.de"

RUN apt-get update
RUN apt-get -y install wget git

ARG TARGETARCH
ENV MINIFORGE_DIR=/opt/conda

RUN if [ "$TARGETARCH" = "amd64" ]; then \
        URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
        URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh"; \
    else \
        echo "Unsupported architecture: $TARGETARCH"; exit 1; \
    fi && \
    wget -O /tmp/miniforge.sh $URL && \
    bash /tmp/miniforge.sh -b -p $MINIFORGE_DIR && \
    rm /tmp/miniforge.sh && \
    $MINIFORGE_DIR/bin/conda clean -afy

# Make it accessible to random UID (OpenShift)
RUN chgrp -R 0 $MINIFORGE_DIR && chmod -R g+rwX $MINIFORGE_DIR \
    && find $MINIFORGE_DIR -type d -exec chmod g+sx {} +

ENV PATH=$MINIFORGE_DIR/bin:$PATH

RUN conda --version
RUN conda config --add channels conda-forge
RUN conda config --set channel_priority strict

RUN git clone https://github.com/euro-cordex/cordex-etl.git

WORKDIR /cordex-etl/
# Copy ETL script
COPY /scripts/run.py /app/run.py
# Copy optional entrypoint script
COPY /scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app
ENTRYPOINT ["./entrypoint.sh"]
