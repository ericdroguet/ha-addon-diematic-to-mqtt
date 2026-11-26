FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY app.py /app/app.py
COPY fuel_monitor.py /app/fuel_monitor.py
COPY stability.py /app/stability.py
COPY thermal_monitor.py /app/thermal_monitor.py
COPY thermal_model.py /app/thermal_model.py
COPY test_thermal_model.py /app/test_thermal_model.py
COPY solar_monitor.py /app/solar_monitor.py
COPY test_solar_monitor.py /app/test_solar_monitor.py
COPY solar_forecast.py /app/solar_forecast.py
COPY test_solar_forecast.py /app/test_solar_forecast.py
RUN python -m py_compile app.py thermal_monitor.py thermal_model.py stability.py fuel_monitor.py solar_monitor.py solar_forecast.py && python -m unittest -v test_thermal_model test_solar_monitor test_solar_forecast
CMD ["python3", "-u", "/app/app.py"]
