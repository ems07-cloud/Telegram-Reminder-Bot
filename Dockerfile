FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY reminder ./reminder
ENV DB_PATH=/data/reminders.sqlite
VOLUME /data
CMD ["python", "-m", "reminder"]
