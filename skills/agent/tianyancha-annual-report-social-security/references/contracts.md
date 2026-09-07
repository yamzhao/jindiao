# Contracts

The input must contain an exact numeric Tianyancha subject, an RFC 3339 query timestamp, and a report cutoff date. The output preserves one of three source states: `verified_records`, `verified_empty`, or `source_error`.

`verified_records` carries a normalized report and at least one Evidence item. `verified_empty` carries no Evidence and no error. `source_error` carries an error and no report or Evidence. All returned URLs and Evidence references must originate from `https://www.tianyancha.com/annualReport/...`.
