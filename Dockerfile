FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py core.py office_api.py tool_hub.py worker.py openrouter_provider.py publication_gate.py workflow_builder.py office3d_api.py agent_tool_providers.py computer_agent.py computer_executor.py robot_vision.py robot_sandbox.py team_collaboration.py agent_work_orders.py repo_agent_bindings.py department_tools.py agent_runtime_pilot.py postiz_verifier.py dashboard.html ./
COPY static ./static
COPY templates ./templates
RUN mkdir -p /data && useradd --system --uid 10001 --create-home lhm && chown 10001:10001 /data
USER 10001
EXPOSE 7770
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7770", "--workers", "1"]
