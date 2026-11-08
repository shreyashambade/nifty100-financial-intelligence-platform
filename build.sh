#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt
python webapp/manage.py collectstatic --no-input
python webapp/manage.py migrate
