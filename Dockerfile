FROM python:3.12

WORKDIR /app
COPY . .
RUN pip3 install -r requirements.txt && cp template-sdx_config.yml sdx_config.yml

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "sdx:app", "--log-level", "debug"]
