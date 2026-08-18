# Build image: renders the static site from the qanda database into /out.
# Build context is the project root (qanda/), so build/ and assets/ are visible.
FROM python:3.12-slim

WORKDIR /app
COPY build/requirements.txt build/requirements.txt
RUN pip install --no-cache-dir -r build/requirements.txt

COPY build/ build/
COPY assets/ assets/

ENV QA_OUT_DIR=/out
WORKDIR /app/build
CMD ["python", "generate.py"]
