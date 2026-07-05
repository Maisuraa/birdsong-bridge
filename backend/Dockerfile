FROM python:3.12-slim

WORKDIR /app

# Add libsndfile1 alongside ffmpeg for librosa
RUN apt-get update && apt-get install -y ffmpeg libsndfile1

# Add resampy to the audio math libraries
COPY backend/requirements.txt backend-requirements.txt
RUN pip install --no-cache-dir tensorflow-cpu librosa resampy
RUN pip install --no-cache-dir birdnetlib==0.18.1
RUN pip install --no-cache-dir -r backend-requirements.txt

COPY backend/ backend/
COPY app/ app/

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backend.main:api --host 0.0.0.0 --port ${PORT}"]