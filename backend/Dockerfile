FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY backend/app /app/app
COPY config /config
COPY data/fixtures /data/fixtures
COPY data/scenarios /data/scenarios
ENV PYTHONPATH=/app
EXPOSE 8100
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
