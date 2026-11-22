FROM node:24-bookworm-slim AS explorer
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGENTSHIELD_DB=/data/events.sqlite3
WORKDIR /app
COPY requirements.txt ./
COPY sdk ./sdk
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 shield && mkdir /data && chown shield /data
COPY backend ./backend
COPY --from=explorer /src/dist ./frontend/dist
USER shield
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
