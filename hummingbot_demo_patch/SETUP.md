# Hummingbot Demo Patch — Setup and Verification

## What this patch does

Adds `bybit_perpetual_demo` domain to `bybit_perpetual_constants.py`, pointing to
`https://api-demo.bybit.com`. Mounted via volume into the Hummingbot demo container.

## Pinned image

`hummingbot/hummingbot:2.3.0` — do not change without re-running the verification below.

## Mount path

```yaml
volumes:
  - ./hummingbot_demo_patch/bybit_perpetual_constants.py:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py:ro
```

## Step 1: Find constants file in pinned image

```bash
docker run --rm hummingbot/hummingbot:2.3.0 \
  find /hummingbot_src/hummingbot/connector/derivative/bybit_perpetual \
  -name "*.py" | sort
```

Confirm `bybit_perpetual_constants.py` exists at the expected path.

## Step 2: Inspect REST_URLS in constants

```bash
docker run --rm hummingbot/hummingbot:2.3.0 \
  grep -n "REST_URLS\|api-testnet\|api\.bybit" \
  /hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

## Step 3: Copy original constants file

```bash
docker create --name hb_tmp hummingbot/hummingbot:2.3.0
docker cp hb_tmp:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py \
  hummingbot_demo_patch/bybit_perpetual_constants.py
docker rm hb_tmp
```

## Step 4: Add demo URL to the copied file

Edit `hummingbot_demo_patch/bybit_perpetual_constants.py` and add to `REST_URLS`:
```python
"bybit_perpetual_demo": "https://api-demo.bybit.com",
```

## Step 5: Verify after stack starts

```bash
docker exec hummingbot-demo python -c \
  "from hummingbot.connector.derivative.bybit_perpetual.bybit_perpetual_constants import REST_URLS; \
   print(REST_URLS.get('bybit_perpetual_demo'))"
```

Expected: `https://api-demo.bybit.com`

## Migration to live

For live trading, `bybit_perpetual_main` already exists natively in the stock image —
no patch needed. Use `docker-compose.live.yml` (future) with standard image and no volume mount.
