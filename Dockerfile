FROM python:3.12-slim

USER 0

WORKDIR /app
COPY . .

RUN chgrp -R 0 /app && \
    chmod -R g=u /app && \
    pip install --no-cache-dir .

USER 1001

ENTRYPOINT ["python", "jobs/etl.py"]
CMD ["--variable-id", "tas", "--frequency", "mon", "--domain-id", "EUR-12"]
