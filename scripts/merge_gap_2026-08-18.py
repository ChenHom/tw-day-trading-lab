"""Fill the pre-collector gap in data/bars with provider kbars.

The collector started at 10:43, so 09:00-10:42 has no tick bar. Provider bars
are appended only where a tick bar is absent, plus one CORRECTED revision for
the boundary bucket the collector joined mid-way through.
"""
import dataclasses
from pathlib import Path
from tw_day_trading_lab.bars import load_latest_bars, append_bars

DATE = "2026-08-18"
SYMS = ("2330", "2317", "2454")

for tf in ("1m", "5m"):
    out = []
    for sym in SYMS:
        mine = sorted(load_latest_bars(Path("data/bars"), timeframe=tf, trading_date=DATE, symbols=[sym]),
                      key=lambda b: b.start_at)
        prov = {b.start_at: b for b in load_latest_bars(Path("data/provider-bars"), timeframe=tf,
                                                        trading_date=DATE, symbols=[sym])}
        cut = mine[0].start_at
        add = [dataclasses.replace(b, source=b.source + "_gapfill")
               for t, b in sorted(prov.items()) if t < cut]
        if cut in prov:  # collector joined mid-bucket; provider's is the whole bucket
            add.append(dataclasses.replace(prov[cut], status="CORRECTED",
                                           revision=mine[0].revision + 1,
                                           source=prov[cut].source + "_gapfill"))
        print(f"{tf} {sym}: mine_from={cut[11:]} gapfill={len(add)}")
        out += add
    append_bars(Path("data/bars"), out)
