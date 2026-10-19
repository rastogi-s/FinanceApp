# Finance App

A private, self-hosted, read-only view of the household's money: balances, holdings, spending, budget,
savings rate and a net-worth projection, with price and budget alerts sent to your phone.

It runs as one Docker image next to Postgres on a small server you own, reachable only over Tailscale.
Nothing in it can move money: every connection is read-only.

The build plan lives in the [Finance App Execution Plan](https://claude.ai/code/artifact/cf425499-0011-45e0-af13-d8e20bcbc76b).
