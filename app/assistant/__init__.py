"""Assistant layer — the LLM lives inside the main app now.

Previously this was a second Flask service (`llm_webapp/`) that talked to the
facility API over HTTPS via `FACILITY_API_BASE`. Both things are now one
process: the tools call this same server on localhost, so a deployment has one
URL, one database, one token, and no cross-service CORS or token duplication.

Nothing here is required for the reporter or officer paths to work. If no
`OPENAI_API_KEY` is set, chat returns a mock reply and the monitor falls back to
deterministic rules — the platform never depends on the model to function.
"""
