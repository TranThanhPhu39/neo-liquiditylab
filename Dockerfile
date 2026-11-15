FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000 MONITOR_DATA_ROOT=/tmp/liquiditylab
WORKDIR /app
COPY deploy/render/requirements.txt /app/requirements-web.txt
RUN pip install --no-cache-dir -r /app/requirements-web.txt
COPY src /app/src
COPY config /app/config
COPY scripts /app/scripts
COPY web/monitor /app/web/monitor
RUN useradd --create-home --uid 10001 monitor
USER monitor
EXPOSE 10000
CMD ["python", "-m", "scripts.serve_monitor_cloud"]
