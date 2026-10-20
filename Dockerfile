# Runtime image for the Lepro workshop (web.server). Only Python + deps live in
# the image; the project directory is bind-mounted at /app (see
# docker-compose.yml) so config.json, certs/, presets/, motions.json and
# animations.json persist on the host and code changes need only a restart.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8081
CMD ["python", "-m", "web.server"]
