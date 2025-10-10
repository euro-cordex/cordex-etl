FROM ubuntu:24.04
ENV PATH="/root/miniforge3/bin:${PATH}"
ARG PATH="/root/miniforge3/bin:${PATH}"

LABEL maintainer="lars.buntemeyer@hereon.de"

RUN apt-get update
RUN apt-get install -y software-properties-common
RUN apt-add-repository -y universe
RUN apt-get update

RUN apt-get -y install wget git

RUN wget \
    https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh \
    && mkdir /root/.conda \
    && bash Miniforge3-Linux-x86_64.sh -b \
    && rm -f Miniforge3-Linux-x86_64.sh
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