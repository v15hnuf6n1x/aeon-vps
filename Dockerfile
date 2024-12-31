FROM 5hojib/aeon:latest #v15hnuf6n1x/aeon:dev

WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
CMD ["bash", "start.sh"]
