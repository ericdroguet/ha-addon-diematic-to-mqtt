FROM python:3.12-alpine
WORKDIR /app
COPY app.py policy.py test_policy.py test_app.py /app/
RUN python -m py_compile app.py policy.py && python -m unittest -v test_policy test_app
CMD ["python", "-u", "/app/app.py"]
