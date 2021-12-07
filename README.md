# pricing-server

## About
This tool imports history of swap transactions from Substrate node into relational database and provides HTTP API for quering last pricing info.

## Usage

1. Clone repository
```bash
git clone https://github.com/sora-xor/pricing-server
cd pricing-server/
```

2. Create `.env` file with DB connection settings:

```bash
cat > .env <<EOF
DB_NAME=sora
DB_USER=sora
DB_PASSWORD=secret
EOF
```

3. Run all services
```bash
docker-compose up
```
This will start Substrate Node, PostreSQL and Web server and will start importing swap history in background.

4. Web server with pricing data will be available at http://localhost/

http://localhost/graph - GraphQL API

http://localhost/pairs/ - Pricing data of all pairs

http://localhost/pairs/{BASE}-{QUOTE}- Pricing data for specific pair. For example: http://localhost/pairs/VAL-XOR/

http://localhost/healthcheck - Healthcheck endpoint. Returns 200 OK. Can be used to check if web server is running and accepting connections.

## Running tests

```bash
python -munittest  # in project directory
```

## Troubleshoot
When certain block are not being processed or no blocks at all then most likely there is a missing or invalid type definition in the `custom_types.json`

Use the `query.py` file. It can be run with:
```bash
cd harvester
python -m pip install -r requirements.txt
python query.py
```


## System requirements

8GB RAM
10GB free disk space
