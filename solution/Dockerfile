# Reproducible runtime for the Bulgarian electricity forecasting pipeline.
#
# Build from the REPO ROOT so the build context includes data/:
#   docker build -f solution/Dockerfile -t bg-forecast .
# Run, mounting outputs back to the host:
#   docker run --rm -v "$(pwd)/solution/outputs:/app/solution/outputs" bg-forecast
#
# (docker compose -f solution/docker-compose.yml up wraps both steps.)

FROM python:3.12-slim

# Faster, quieter, deterministic Python; fixed seed already set in code.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) Dependencies first (cached layer; only re-runs when requirements change).
COPY solution/requirements.txt /app/solution/requirements.txt
RUN pip install --no-cache-dir -r /app/solution/requirements.txt

# 2) Code + the seed data the pipeline reads from ../data.
COPY solution/ /app/solution/
COPY data/ /app/data/

# config.py resolves REPO_ROOT to /app, so data lives at /app/data and outputs
# at /app/solution/outputs. Run the pipeline, then build report.html into the
# (mounted) outputs folder so the host can open it. A container can't open a
# browser on the host -- use solution/run.sh, which runs this then opens it.
CMD ["sh", "-c", "python -m solution.run_pipeline && python -m solution.build_report"]
