#!/bin/sh

alembic upgrade head || exit

python run_node_processing.py -f -b 109466
