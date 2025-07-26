FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# When building the image the context is the `sales_agent_api` directory, so the
# package itself is located at `/app`. Run uvicorn using the local package name
# rather than `sales_agent_api`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
