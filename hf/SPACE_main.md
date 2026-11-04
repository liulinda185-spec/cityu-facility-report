---
title: CityU Facility Report
emoji: 🏢
colorFrom: red
colorTo: rose
sdk: docker
app_port: 7860
pinned: false
---

# CityU Facility Report

Facility issue reporting system (FastAPI + SQLite + scikit-learn) for the YKM 4/F Concourse pilot (LEAD Scheme).

- `/` — submit a facility report (with photo, AI category suggestion)
- `/track` — track a ticket by code
- `/officer` — officer queue (claim / resolve / recategorize / duplicates / compile); requires the `OFFICER_TOKEN` secret

Set `OFFICER_TOKEN` under **Settings → Variables and secrets** before use.
