FROM python:3.12-alpine
WORKDIR /app
COPY app.py test_app.py /app/
RUN python -m py_compile app.py && python -m unittest -v test_app
CMD ["python3","-u","/app/app.py"]
