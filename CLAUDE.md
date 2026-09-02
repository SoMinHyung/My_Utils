# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

My_Utils is a collection of Python utility modules, primarily focused on cryptocurrency trading automation across multiple exchanges (Upbit, Binance, Gate.io) with Telegram-based alerts and monitoring.

## Repository Structure

- **openAPI/** — Primary module: multi-exchange crypto trading bot with automated listing detection, order execution, wallet monitoring, and a PyQt5 GUI
- **etherscan/** — Ethereum blockchain wallet address analysis from CSV transaction data
- **crawler/** — Telegram-notifying web monitors sharing ONE uv env (Python 3.12), `common/` code, and one `secrets.ini`. Run any with `uv run python <path>`; deps in `crawler/pyproject.toml`:
  - `interpark_monitor/`, `doosan_ticket_monitor/` — Playwright ticket-seat monitors
  - `kofia_notice_bot/` — KOFIA notice-board watcher (requests)

## Key Architecture (openAPI/)

Exchange clients are implemented as classes with authentication wrappers:
- `upbit.py` — Upbit API client (JWT auth, market orders, wallet management, withdrawals)
- `binance.py` — Binance client via CCXT library (limit/market orders, position sizing)
- `gateio.py` — Gate.io client (HMAC-SHA512 auth)

Automated monitoring loops:
- `crawling_notice.py` / `crawling_upbit_notice.py` — Poll Upbit for new listing announcements ("[거래]"), auto-execute market buys, send Telegram alerts
- `0624upbit.py` — DepositAlert class monitoring wallet state transitions (paused→working)
- `main.py` — Orchestrator integrating Telegram message parsing with notice crawling and trade execution

GUI:
- `make_gui.py` — PyQt5 app for real-time wallet status monitoring (3s refresh via QTimer)

## Dependencies

Install via: `pip install -r openAPI/requirements.txt`

Key libraries: PyQt5, ccxt, pyjwt, requests, python-telegram-bot, telethon

## Configuration

- API keys stored in `config.ini` and `key.txt` files (not committed)
- Telegram bot credentials in `config.ini` (api_id, api_hash, bot token)
- Exchange credentials use access_key / secret_key pairs

## Language

Code comments, variable names, and announcement parsing use Korean (e.g., "[거래]" = trading listing, "원화" = KRW market).
