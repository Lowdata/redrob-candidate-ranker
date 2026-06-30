FROM python:3.11-slim

WORKDIR /app

# Copy dependency list
COPY requirements.txt .

# Install test dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Set default command (can be overridden)
ENTRYPOINT ["python", "rank.py"]
