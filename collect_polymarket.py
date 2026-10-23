"""Daily prices of every Polymarket outcome token from the on-chain fills (Envio dataset on Hugging Face, CC-BY),
January to September 2024, plus the metadata and the resolution of each market.
DuckDB reads only the needed columns over HTTPS and aggregates while streaming."""
from pathlib import Path

import duckdb

out = Path(__file__).resolve().parent / "data" / "polymarket"
out.mkdir(parents=True, exist_ok=True)
base = "https://huggingface.co/datasets/moose-code/polymarket-onchain-v1/resolve/main/"
con = duckdb.connect()
con.sql("load httpfs; set threads = 4; set memory_limit = '3GB';")

for mes in [f"{m:02d}" for m in range(1, 10)]:
    destino = out / f"daily_2024_{mes}.parquet"
    if destino.exists():
        continue
    # one side of each fill is USDC (asset id 0): price = USDC paid / tokens received, amounts with 6 decimals
    con.sql(f"""
        copy (
            with f as (
                select cast(timestamp as bigint) as ts,
                       case when makerAssetId = '0' then takerAssetId else makerAssetId end as token,
                       case when makerAssetId = '0' then cast(makerAmountFilled as double) else cast(takerAmountFilled as double) end / 1e6 as usdc,
                       case when makerAssetId = '0' then cast(takerAmountFilled as double) else cast(makerAmountFilled as double) end / 1e6 as tokens
                from read_parquet('{base}order_filled/year=2024/month={mes}.parquet')
                where makerAssetId = '0' or takerAssetId = '0'
            )
            select token, cast(to_timestamp(ts) as date) as day,
                   sum(usdc) / sum(tokens) as vwap, arg_max(usdc / tokens, ts) as last_price,
                   sum(usdc) as volume_usdc, count(*) as fills
            from f where tokens > 0
            group by 1, 2
        ) to '{destino.as_posix()}' (format parquet)
    """)
    print("month", mes, "done", flush=True)

for t in ["market_data", "condition"]:
    destino = out / f"{t}.parquet"
    if not destino.exists():
        con.sql(f"copy (select * from read_parquet('{base}{t}.parquet')) to '{destino.as_posix()}' (format parquet)")
        print(t, "done", flush=True)

# resolution of the binary markets that ended in February to September 2024 (the two files above are 500 MB, this one is small).
# The ConditionalTokens contract gives the outcome tokens of each condition (positionIds) and their payout (numerators / denominator)
destino = out / "resolved_2024.parquet"
if not destino.exists():
    con.sql(f"""
        copy (
            with c as (
                select id as condition, positionIds, payoutNumerators, cast(payoutDenominator as double) as den
                from '{(out / "condition.parquet").as_posix()}'
                where cast(payoutDenominator as double) > 0 and len(positionIds) = 2
            )
            select m.id as token, m.marketName as market, m.outcomes, cast(m.endDate as timestamp) as end_date,
                   cast(c.payoutNumerators[1] as double) / c.den as payout
            from '{(out / "market_data.parquet").as_posix()}' m
            join c on m.condition = c.condition and m.id = c.positionIds[1]
            where m.endDate between '2024-02-01' and '2024-09-30'
        ) to '{destino.as_posix()}' (format parquet)
    """)
    print("resolved markets done", flush=True)
