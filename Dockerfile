FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY skills /app/skills
COPY docs /app/docs

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-lc", "research-registry-migrate && research-registry-web"]
