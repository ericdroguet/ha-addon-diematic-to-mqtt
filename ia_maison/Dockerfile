FROM python:3.12-alpine
WORKDIR /app
COPY app.py thermal.py test_app.py test_thermal.py dashboard.html /app/
RUN python -m py_compile app.py thermal.py && python -m unittest -v test_app test_thermal
CMD ["python3","-u","/app/app.py"]
