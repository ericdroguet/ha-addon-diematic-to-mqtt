ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}
WORKDIR /app
COPY app.py /app/app.py
CMD ["python3", "-u", "/app/app.py"]
