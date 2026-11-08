FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY app.py /app/app.py
COPY fuel_monitor.py /app/fuel_monitor.py
COPY stability.py /app/stability.py
CMD ["python3", "-u", "/app/app.py"]
