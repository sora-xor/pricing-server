FROM python:3.8

RUN useradd -ms /bin/bash app

USER app

WORKDIR /home/app

ENV PATH="/home/app/.local/bin:${PATH}"

COPY --chown=app:app requirements.txt /home/app/requirements.txt
RUN pip3 install --user -r requirements.txt
COPY --chown=app:app *.py /home/app/
COPY --chown=app:app custom_types.json /home/app/
COPY --chown=app:app start.sh /home/app/start.sh
COPY --chown=app:app alembic.ini /home/app/alembic.ini
COPY --chown=app:app alembic/*.py /home/app/alembic/
COPY --chown=app:app alembic/versions/*.py /home/app/alembic/versions/

RUN pip3 install --user gunicorn
