# Simple Dashboard

A minimal Streamlit dashboard for testing the DevOps Hero deployment pipeline.

## What This Is

This is a **Track 1 MVP app** — the simplest possible application to prove the deployment pipeline works end-to-end:

- ✅ Single Python file
- ✅ No database
- ✅ No external services
- ✅ No secrets required
- ✅ Just serves a web UI on port 8501

## Local Development

```bash
cd app

# Run with uv (auto-creates venv and installs deps)
uv run streamlit run app.py
```

Then visit http://localhost:8501

That's it! `uv run` reads `pyproject.toml`, creates a `.venv` if needed, installs dependencies, and runs the command — all in one step.

## Docker Build

```bash
# Build the image
docker build -t simple-dashboard .

# Run the container
docker run -p 8501:8501 simple-dashboard
```

## Deployment with DevOps Hero

This app is designed to be deployed via DevOps Hero's deployment pipeline:

1. Build → Docker image created
2. Push → Image pushed to ECR
3. Deploy → Fargate task/service created
4. URL → User gets a working URL

