# 02 — Exchange API & Market Data Pipeline

This document explains how the bot interacts with the Roostoo mock cryptocurrency exchange, how requests are authenticated and signed with HMAC-SHA256, and how the simulated Paper Trading engine mimics real exchange behavior.

---

## 1. Roostoo Exchange API Overview

The Roostoo exchange backend runs a REST API simulating spot cryptocurrency trading.

- **Base URL**: `https://mock-api.roostoo.com`
- **Data Format**: JSON for all responses and standard URL-encoded form data for signed POST bodies.
- **Timestamp Standard**: 13-digit millisecond Unix timestamp (`int(time.time() * 1000)`).
- **Rate Limits & Headers**: All requests send a standard `User-Agent: TradeBot/1.0`.

---

## 2. Authentication & Request Signing

Roostoo endpoints fall into three security tiers:

1. **Public (Unsigned)**: No API keys or signatures needed (e.g., `/v3/serverTime`, `/v3/exchangeInfo`).
2. **Timestamp Checked (`RCL_TSCheck`)**: Requires a `timestamp` query parameter to prevent replay attacks, but no signature (e.g., `/v3/ticker`).
3. **Signed Endpoints (`RCL_TopLevelCheck`)**: Requires an API Key header and an HMAC-SHA256 signature calculated over the sorted parameter payload.

### The HMAC-SHA256 Signing Algorithm

The signing routine is implemented in [`RoostooClient._sign()`](file:///home/ayush/Practice/trade-bot/src/api/client.py#L41-L55):

```python
def _sign(self, params: dict) -> tuple[dict, str, str]:
    # 1. Attach current 13-digit millisecond timestamp
    params["timestamp"] = str(int(time.time() * 1000))
    
    # 2. Sort keys alphabetically
    sorted_keys = sorted(params.keys())
    
    # 3. Concatenate into a query string: key1=val1&key2=val2...
    total_params = "&".join(f"{k}={params[k]}" for k in sorted_keys)
    
    # 4. Compute HMAC-SHA256 hex digest using secret key
    signature = hmac.new(
        self.secret_key.encode("utf-8"),
        total_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    return params, total_params, signature
```

### Request Headers for Signed Endpoints:
```http
RST-API-KEY: <your_roostoo_api_key>
MSG-SIGNATURE: <hmac_sha256_hex_digest>
Content-Type: application/x-www-form-urlencoded  (for POST requests)
```

---

## 3. Detailed Endpoint Breakdown

All API wrappers are encapsulated in [`src/api/client.py`](file:///home/ayush/Practice/trade-bot/src/api/client.py).

### 3.1 Public Endpoints

#### `GET /v3/serverTime` (`client.server_time()`)
- **Purpose**: Verify network connectivity and check clock synchronization with the server.
- **Response**: `{"ServerTime": 1790800000000}`

#### `GET /v3/exchangeInfo` (`client.exchange_info()`)
- **Purpose**: Retrieve rules, trade limits, and precision for all listed currency pairs.
- **Key Fields Per Pair**:
  - `CanTrade`: Boolean indicating if trading is currently active.
  - `AmountPrecision`: Number of decimal places allowed for order quantities (e.g., `4` for BTC, `2` for SOL, `0` for BONK).
  - `MiniOrder`: Minimum order size in USD (typically `$1.00`).

#### `GET /v3/ticker` (`client.ticker(pair=None)`)
- **Purpose**: Retrieve real-time best bid, best ask, last traded price, and 24h volume.
- **Security**: Requires `timestamp` query parameter.
- **Sample Response Structure**:
  ```json
  {
    "Data": {
      "BTC/USD": {
        "LastPrice": 84250.00,
        "MaxBid": 84248.50,
        "MinAsk": 84251.50,
        "Change": 0.0145,
        "CoinTradeValue": 124.5,
        "UnitTradeValue": 10489125.0
      }
    }
  }
  ```

---

### 3.2 Signed Account & Order Endpoints

#### `GET /v3/balance` (`client.balance()`)
- **Purpose**: Check current free and locked wallet balances for USD and all coins.
- **Sample Response Structure**:
  ```json
  {
    "Success": True,
    "SpotWallet": {
      "USD": {"Free": 42500.50, "Lock": 0.0},
      "BTC": {"Free": 0.0850, "Lock": 0.0}
    }
  }
  ```

#### `POST /v3/place_order` (`client.place_order(pair, side, quantity, order_type, price=None)`)
- **Parameters**:
  - `pair`: Trading pair symbol (e.g., `"BTC/USD"`).
  - `side`: `"BUY"` or `"SELL"`.
  - `type`: `"MARKET"` or `"LIMIT"`.
  - `quantity`: String representation of the quantity, rounded to `AmountPrecision`.
  - `price`: String representation of the limit price (required if `type="LIMIT"`).
- **Sample Fill Response**:
  ```json
  {
    "Success": True,
    "OrderDetail": {
      "Pair": "BTC/USD",
      "OrderID": "1048576",
      "Status": "FILLED",
      "Side": "BUY",
      "Type": "MARKET",
      "FilledQuantity": 0.085,
      "FilledAverPrice": 84250.00,
      "CommissionChargeValue": 7.16125,
      "CommissionPercent": 0.001
    }
  }
  ```

#### `POST /v3/query_order` (`client.query_order()`)
- **Purpose**: Inspect order history or check execution status of pending limit orders.

#### `POST /v3/cancel_order` (`client.cancel_order()`)
- **Purpose**: Cancel a pending order by `order_id` or cancel all pending orders for a given `pair`.

---

## 4. Paper Trading Engine (`PaperTrader`)

The [`PaperTrader`](file:///home/ayush/Practice/trade-bot/src/paper_trader.py) class is a drop-in local replacement for live exchange order calls. When `PAPER_MODE = True` in `src/main.py`, all order executions bypass the network and run locally in memory.

### Key Simulation Mechanics:

1. **Real Live Prices**: The simulation uses the actual real-time `LastPrice` polled from the exchange ticker. It does not use synthetic or random price data.
2. **Realistic Fee Modeling**:
   - Market orders are charged **0.10% Taker Fee** (`TAKER_FEE = 0.001`).
   - Limit orders are charged **0.05% Maker Fee** (`MAKER_FEE = 0.0005`).
   - Total buying cost = `Quantity * Price + Commission`.
   - Total selling proceeds = `Quantity * Price - Commission`.
3. **Position Cost Averaging**: When accumulating into an existing coin holding, `PaperTrader` dynamically updates the weighted average entry price:
   $$\text{New Avg Entry} = \frac{(\text{Existing Qty} \times \text{Existing Avg}) + (\text{New Qty} \times \text{New Price})}{\text{Existing Qty} + \text{New Qty}}$$
4. **Interface Parity**: The `PaperTrader.place_order()` and `PaperTrader.balance()` methods return dictionaries that match the exact schema returned by the live Roostoo REST API (`OrderDetail`, `SpotWallet`, `Success`, `CommissionChargeValue`).

---

## 5. Precision & Minimum Order Rules

To prevent orders from being rejected by the exchange, the bot applies two transformations before placing any trade:

1. **Quantity Truncation via `AmountPrecision`**:
   Quantities are strictly truncated (never rounded up, which could exceed available cash) to the maximum allowed decimal places:
   ```python
   factor = 10 ** amt_prec
   qty = int((size_usd / price) * factor) / factor
   ```

2. **Minimum Order Value (`MiniOrder`) Check**:
   If `qty * price < MiniOrder`, the order is rejected immediately to prevent API error responses.
