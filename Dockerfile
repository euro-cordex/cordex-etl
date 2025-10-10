FROM ubuntu:24.04
ENV PATH="/root/miniforge3/bin:${PATH}"
ARG PATH="/root/miniforge3/bin:${PATH}"

LABEL maintainer="lars.buntemeyer@hereon.de"

RUN apt-get update
RUN apt-get -y install wget git


RUN wget -O /tmp/miniforge.sh \
    https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm /tmp/miniforge.sh \
    && /opt/conda/bin/conda clean -afy

# Make it accessible to random UID (OpenShift)
RUN chgrp -R 0 /opt/conda && chmod -R g+rwX /opt/conda \
    && find /opt/conda -type d -exec chmod g+sx {} +

ENV PATH=/opt/conda/bin:$PATH

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
