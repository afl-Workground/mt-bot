FROM python:3.10-slim

# 1. Install system dependencies (Git is required)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Copy application files
COPY . .

# 4. Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 5. Run the bot
CMD ["python", "maintainer_bot.py"]
