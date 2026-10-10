FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p output logs

ENTRYPOINT ["python", "pipeline_runner.py"]
CMD ["--skip-health"]