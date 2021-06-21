# pricing-server

## About
This tool imports history of swap transactions from Substrate node into relational database and provides HTTP API for quering last pricing info.

## Usage

1. Clone repository
```bash
git clone https://github.com/yuriiz/pricing-server
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

http://localhost/pairs/ - pricing data of all pairs

http://localhost/pairs/{BASE}-{QUOTE}- pricing data for specific pair. For example: http://localhost/pairs/XOR-PSWAP/

## System requirements

8GB RAM
10GB free disk space
