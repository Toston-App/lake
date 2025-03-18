FROM python:3.10

# Install uv for faster package installation
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/

EXPOSE 80

WORKDIR /app/

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv pip compile pyproject.toml > requirements.txt && \
    uv pip install --system --no-cache -r requirements.txt

COPY ./start.sh /start.sh
RUN chmod +x /start.sh

COPY ./gunicorn_conf.py /gunicorn_conf.py

COPY ./start-reload.sh /start-reload.sh
RUN chmod +x /start-reload.sh

COPY ./app /app
ENV PYTHONPATH=/app

CMD ["/start.sh"]