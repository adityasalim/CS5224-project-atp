FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tp_travel_agent.py .
COPY background_jobs.py .
COPY run_history.py .
COPY gateway_client.py .
COPY prompts.py .
COPY final_evaluation.py .
COPY package_gather.py .
COPY package_cost.py .
COPY package_recommendations.py .
COPY trip_currency.py .
COPY model_configuration.py .
COPY model_adapter.py .
COPY model_budget.py .
COPY google_transport.py .
COPY public_models.py .
COPY public_protocol.py .
COPY public-model-config.json .
COPY public-model-prices.json .
COPY feedback_routing.py .
COPY acceptance.py .
COPY hours_util.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "tp_travel_agent.py"]
