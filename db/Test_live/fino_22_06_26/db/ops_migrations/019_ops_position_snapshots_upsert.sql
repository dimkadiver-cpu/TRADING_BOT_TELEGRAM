-- Replace append-only position snapshots with one current row per account/symbol/side.
-- Uses rename/copy/drop so it works with the repository migration runner and SQLite's
-- limited ALTER TABLE support.

ALTER TABLE ops_position_snapshots RENAME TO ops_position_snapshots_legacy;

CREATE TABLE ops_position_snapshots (
    account_id          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    qty                 REAL,
    mark_price          REAL,
    unrealized_pnl      REAL,
    cum_realized_pnl    REAL,
    source              TEXT NOT NULL,
    captured_at         TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol, side)
);

INSERT INTO ops_position_snapshots (
    account_id,
    symbol,
    side,
    qty,
    mark_price,
    unrealized_pnl,
    cum_realized_pnl,
    source,
    captured_at
)
SELECT
    ranked.account_id,
    ranked.symbol,
    ranked.side,
    ranked.qty,
    ranked.mark_price,
    ranked.unrealized_pnl,
    ranked.cum_realized_pnl,
    ranked.source,
    ranked.captured_at
FROM (
    SELECT
        legacy.account_id,
        legacy.symbol,
        legacy.side,
        CASE
            WHEN json_valid(legacy.payload_json) THEN COALESCE(
                json_extract(legacy.payload_json, '$.qty'),
                json_extract(legacy.payload_json, '$.qty_found'),
                json_extract(legacy.payload_json, '$.contracts'),
                json_extract(legacy.payload_json, '$.size')
            )
            ELSE NULL
        END AS qty,
        CASE
            WHEN json_valid(legacy.payload_json) THEN COALESCE(
                json_extract(legacy.payload_json, '$.mark_price'),
                json_extract(legacy.payload_json, '$.markPrice')
            )
            ELSE NULL
        END AS mark_price,
        CASE
            WHEN json_valid(legacy.payload_json) THEN COALESCE(
                json_extract(legacy.payload_json, '$.unrealized_pnl'),
                json_extract(legacy.payload_json, '$.unrealizedPnl')
            )
            ELSE NULL
        END AS unrealized_pnl,
        CASE
            WHEN json_valid(legacy.payload_json) THEN COALESCE(
                json_extract(legacy.payload_json, '$.cum_realized_pnl'),
                json_extract(legacy.payload_json, '$.cumRealizedPnl')
            )
            ELSE NULL
        END AS cum_realized_pnl,
        legacy.source,
        legacy.captured_at,
        ROW_NUMBER() OVER (
            PARTITION BY legacy.account_id, legacy.symbol, legacy.side
            ORDER BY datetime(legacy.captured_at) DESC, legacy.captured_at DESC, legacy.snapshot_id DESC
        ) AS row_num
    FROM ops_position_snapshots_legacy AS legacy
) AS ranked
WHERE ranked.row_num = 1;

DROP TABLE ops_position_snapshots_legacy;
