# GRUS — AgentCore runtime image
#
# AgentCore runs ARM64. Building on an x86 laptop needs buildx with
# --platform linux/arm64, which the deploy script handles.

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

# psycopg needs libpq at runtime. The -binary wheel bundles it, but the
# system library is still required for some paths.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-agentcore.txt .
RUN pip install --no-cache-dir -r requirements-agentcore.txt

# The agent and everything it imports.
COPY grus_agentcore.py .
COPY grus_agents.py .
COPY grus_tools.py .
COPY grus_retriever.py .
COPY grus_rules.py .
COPY grus_chat.py .
COPY grus_risk_score.py .
COPY grus_composer.py .
COPY grus_config.py .
# Model artefacts for the risk scorer. Without these it reports
# 'unavailable' rather than failing, but the scores are worth having.
COPY model/models/features.json  model/models/
COPY model/models/endpoints.json model/models/
COPY model/models/thresholds.json model/models/
COPY model/models/report.json    model/models/

ENV PYTHONUNBUFFERED=1
ENV AWS_REGION=ap-south-1

EXPOSE 8080

CMD ["python", "grus_agentcore.py"]