# Use the official Python lightweight image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir pymysql python-multipart

# Copy the rest of the application code
COPY . .

# IMPORTANT: Ensure the GCS credentials key file is copied and correctly referenced
# This points exactly to where you placed the file in the app directory
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/app/worksphere-490606-3667ec0def14.json"

# Expose port (Cloud Run expects port 8080 by default)
EXPOSE 8080

# Command to run the application using Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
