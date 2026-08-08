FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml ./
COPY webapp ./webapp
COPY modeling ./modeling
COPY scrapers ./scrapers
COPY db ./db
COPY static ./static
COPY flags.py ./flags.py

RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
CMD ["uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
