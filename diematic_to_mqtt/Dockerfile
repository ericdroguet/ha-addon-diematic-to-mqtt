FROM python:3.12-alpine

WORKDIR /app

COPY src/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY src/*.py ./
COPY src/ ./src/
COPY tests/ ./tests/
RUN python3 -m compileall -q . && python3 -m unittest discover -s tests -v && rm -rf tests src
COPY src/conf/logging.conf ./conf/logging.conf

COPY render_config.py /render_config.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
