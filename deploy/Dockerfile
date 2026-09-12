FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements.txt
COPY demo/requirements.txt demo-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r demo-requirements.txt

COPY pyproject.toml pyproject.toml
COPY src src
RUN pip install --no-cache-dir -e .

COPY demo demo

EXPOSE 8501
CMD ["streamlit", "run", "demo/dashboard.py", "--server.address=0.0.0.0"]
