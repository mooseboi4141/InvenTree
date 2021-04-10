#!/bin/sh

echo "Starting InvenTree worker..."

sleep 5

# Wait for the database to be ready
cd src/InvenTree

python manage.py wait_for_db

sleep 10

# Now we can launch the background worker process
python manage.py qcluster
