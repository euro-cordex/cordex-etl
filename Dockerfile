FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "jobs/etl.py"]
CMD ["--variable-id", "tas", "--frequency", "mon", "--domain-id", "EUR-12"]
