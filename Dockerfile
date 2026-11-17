FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade 'pip>=26.2,<27' && pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 alart && mkdir -p /srv/data && chown alart:alart /srv/data
COPY alart ./alart
COPY db ./db
COPY web ./web
COPY VERSION ./VERSION
USER alart
CMD ["uvicorn", "alart.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
