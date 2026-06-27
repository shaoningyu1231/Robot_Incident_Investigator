# Robot Incident Investigator — Cloud Run image.
# Runtime only serves pre-exported incident/ assets + drives the Gemini API; it does
# not generate bags or render charts, so no rosbags/numpy/Pillow are installed.
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# app code + tools/incident_rules.py + exported incident/ assets (per .dockerignore)
COPY . .

# Cloud Run injects $PORT (default 8080); app.py binds 0.0.0.0:$PORT
ENV PORT=8080
EXPOSE 8080
CMD ["python", "backend/app.py"]
