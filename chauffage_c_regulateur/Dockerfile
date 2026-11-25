FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY app.py /app/app.py
COPY fuel_monitor.py /app/fuel_monitor.py
COPY stability.py /app/stability.py
COPY thermal_monitor.py /app/thermal_monitor.py
COPY thermal_model.py /app/thermal_model.py
COPY test_thermal_model.py /app/test_thermal_model.py
RUN python -m py_compile app.py thermal_monitor.py thermal_model.py stability.py fuel_monitor.py && python -m unittest -v test_thermal_model
CMD ["python3", "-u", "/app/app.py"]
